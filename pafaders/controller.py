from collections import defaultdict


class Controller:
    def __init__(self):
        self.subscribers = defaultdict(set)

    def subscribe(self, message, fn):
        self.subscribers[message].add(fn)

    def set_application_list(self, apps):
        for fn in self.subscribers["set_application_list"]:
            fn(apps)

    def set_volume(self, *, app, volume):
        for fn in self.subscribers["set_volume"]:
            fn(app=app, volume=volume)

    def play_or_pause(self):
        for fn in self.subscribers["play_or_pause"]:
            fn()
