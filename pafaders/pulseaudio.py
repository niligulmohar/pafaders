"""PulseAudio sink-input interface."""

import logging
import threading

import pulsectl


LOG = logging.getLogger(__name__)


class Application:
    def __init__(self, pa_sink_input):
        self.pa_sink_input = pa_sink_input
        self.index = pa_sink_input.index
        self.active = True

    def name(self):
        app_name = self.pa_sink_input.proplist["application.name"]
        media_name = self.pa_sink_input.proplist["media.name"]

        # Same convention as pavucontrol
        return f"{app_name} : {media_name}"

    def identity(self):
        # If sink inputs are named the same, they are allowed to take
        # over faders vacated by removed sink inputs.
        return self.name()

    def may_replace(self, other):
        return self.identity() == other.identity()

    def __repr__(self):
        return f"<Application #{self.index} {self.name()}>"


class Applications:
    def __init__(self, *, controller):
        self.controller = controller
        self.controller.subscribe("set_volume", self.set_volume)
        self.pulse = pulsectl.Pulse("pafaders")
        self.app_by_index = {}
        self.app_list = []

        # We may be called via callback functions in other threads.
        self.lock = threading.Lock()

    def __enter__(self):
        self.pulse.__enter__()
        return self

    def __exit__(self, *args):
        return self.pulse.__exit__(*args)

    def add_sink_input_as_app(self, sink_input):
        new_app = Application(sink_input)
        LOG.debug("Found app %r", new_app)
        self.app_by_index[sink_input.index] = new_app

        # Replace similar app
        for n, app in enumerate(self.app_list):
            if not app.active and new_app.may_replace(app):
                self.app_list[n] = new_app
                return

        # Take position of removed app if we are full
        if len(self.app_list) > 8:
            for n, app in enumerate(self.app_list):
                if not app.active:
                    self.app_list[n] = new_app
                    return

        self.app_list.append(new_app)

    def remove_app(self, app):
        if app.active:
            LOG.debug("Lost app %r", app)
            app.active = False
            return True
        return False

    def update_sink_inputs(self):
        with self.lock:
            changed = False
            sink_input_indices = set()

            for si in self.pulse.sink_input_list():
                sink_input_indices.add(si.index)
                if si.index not in self.app_by_index:
                    self.add_sink_input_as_app(si)
                    changed = True

            removed_indices = set(self.app_by_index).difference(sink_input_indices)
            for index in removed_indices:
                if self.remove_app(self.app_by_index[index]):
                    changed = True

            if changed:
                self.controller.set_application_list(self.app_list)

    def check(self):
        # Poll for updates. We might listen for events instead, but
        # then we cannot use blocking APIs like sink_input_list() at
        # the same time.
        self.update_sink_inputs()

    def set_volume(self, *, app, volume):
        with self.lock:
            try:
                app = self.app_list[app]
            except IndexError:
                return

            if app.active:
                self.pulse.volume_set_all_chans(app.pa_sink_input, volume)
