#!./venv/bin/python
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
import json
import urllib.request
import time
import os
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from dotenv import load_dotenv
import urllib.parse as parse

load_dotenv()

class DisoveryListener:

    def __init__(self, filter=[]):
        self.adresses = []
        self.filter = filter

    def remove_service(self, zeroconf, type, name):
        print("[💡]     Service %s removed" % (name,))

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        adress = info.addresses[0]
        formatted_adress = f'{adress[0]}.{adress[1]}.{adress[2]}.{adress[3]}:{info.port}'
        if len(self.filter) > 0 and any(map(lambda item: item in name, self.filter)):
            print(f'[💡][🔈] Skipping speaker {name}')
            return
        print(f'[💡][🔈] Found speaker: {name} {formatted_adress}')
        if formatted_adress not in self.adresses:
            self.adresses.append(formatted_adress)

    def update_service(self, zeroconf, type, name):
        print("[💡]     Service %s updated" % (name,))

class PubSubListener:

    def __init__(self, DisoveryListener):
        self.eventIds = []
        self.start_time = time.time()

        self.DisoveryListener = DisoveryListener
        self.project_id = os.environ["PROJECT_ID"]
        self.subscription_id = os.environ["SUBSCRIPTION_ID"]

    def startListener(self):
        subscriber = pubsub_v1.SubscriberClient()
        subscriber_path = subscriber.subscription_path(self.project_id, self.subscription_id)
        streaming_pull_future = subscriber.subscribe(subscriber_path, callback=self.callback)
        print(f"[📟]     Listening for messages on {subscriber_path}...")

        with subscriber:
            try:
                streaming_pull_future.result()
            except TimeoutError:
                streaming_pull_future.cancel()
                streaming_pull_future.result()

    def callback(self, message: pubsub_v1.subscriber.message.Message) -> None:
        if time.time() - self.start_time < 120:
            print("[📟][👻] Event was cached, too fast delivered, skipping")
            message.ack()
            return
        payload = json.loads(message.data.decode("utf-8"))
        events = payload['resourceUpdate']['events']
        print(f'[📟][👻] {events}')

        for type in events.keys():
            if type == "sdm.devices.events.DoorbellChime.Chime" and events[type]['eventSessionId'] not in self.eventIds:
                for adress in self.DisoveryListener.adresses:
                    print(f"[📟][👻] Sending notification to {adress}")
                    contents = urllib.request.urlopen(f"http://{adress}/playNotification").read()
                self.eventIds.append(events[type]['eventSessionId'])
        message.ack()

class AccessCheck:

    def __init__(self):
        with open(os.environ["TOKENS_FILE"]) as f:
            file = json.load(f)
            self.access_token = file.get('access_token') or ""
            self.refresh_token = file.get('refresh_token') or ""
            self.expires_in = file.get('expires_in') or 3599
            self.generatedAt = file.get('generated_at') or 0

            self.client_id = os.environ["CLIENT_ID"]
            self.client_secret = os.environ["CLIENT_SECRET"]
            self.device_client_id = os.environ["DEVICE_CLIENT_ID"]

    def checkAccess(self):
        if time.time() - self.generatedAt < self.expires_in:
            print("[👮][🔴] Token expired, refreshing")
            self.refreshAccess()
        else:
            print("[👮][🟢] Token still valid")
        try:
            req = urllib.request.Request(f'https://smartdevicemanagement.googleapis.com/v1/enterprises/{urllib.parse.quote_plus(self.device_client_id)}/devices')
            req.add_header('Authorization', f'Bearer {self.access_token}')
            resp = urllib.request.urlopen(req)
            content = json.loads(resp.read())
            devices = content['devices']
            for i in devices:
                print(f"[👮]     Found device: {i['name'].replace(f"enterprises/{self.device_client_id}/devices/", "")} type: {i['type'].replace('sdm.devices.types.', '')}")
        except urllib.error.URLError as e:
            print(f"[👮]     Error while fetching devices: {e}")
            if e.status == 401:
                self.refreshAccess()
                self.checkAccess()
    
    def refreshAccess(self):
        try:
            payload = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token'
            }
            data = parse.urlencode(payload).encode().decode("utf-8")
            req = urllib.request.Request(f'https://www.googleapis.com/oauth2/v4/token?{data}', data=[])
            resp = urllib.request.urlopen(req)
            content = json.load(resp)
            content['generatedAt'] = time.time()
            content['refresh_token'] = self.refresh_token
            print("[👮]     Token refreshed")
            with open(os.environ["TOKENS_FILE"], 'w') as f:
                f.write(json.dumps(content, indent=4, sort_keys=True, default=str))
            self.access_token = content['access_token']
            self.refresh_token = content['refresh_token']
            self.expires_in = content['expires_in']
            self.generatedAt = content['generatedAt']
        except Exception as e:
            print(f"[👮] Error while refreshing token: {e}")

zeroconf = Zeroconf()
listener = DisoveryListener(filter=[])
pubSubListener = PubSubListener(listener)
accessCheck = AccessCheck()

browser = ServiceBrowser(zeroconf, "_soundtouch._tcp.local.", listener)
accessCheck.checkAccess()
pubSubListener.startListener()