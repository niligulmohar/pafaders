"""PulseAudio sink-input interface."""

import logging
import threading

import pulsectl


LOG = logging.getLogger(__name__)


class Application:
    def __init__(self, *, pa_sink_input):
        self.pa_sink_inputs = []
        self.active_sink_inputs = {}
        self.add_sink_input(pa_sink_input)

    @classmethod
    def get(cls, *, pa_sink_input):
        for subclass in cls.__subclasses__():
            if subclass.handles(pa_sink_input=pa_sink_input):
                app_class = subclass
                break
        else:
            app_class = cls

        return app_class(pa_sink_input=pa_sink_input)

    @classmethod
    def handles(cls, *, pa_sink_input):
        return False

    def name(self):
        app_names = {si.proplist["application.name"] for si in self.pa_sink_inputs}
        media_names = {si.proplist["media.name"] for si in self.pa_sink_inputs}

        if len(app_names) == len(media_names) == 1:
            # Same convention as pavucontrol
            return f"{app_names.pop()} : {media_names.pop()}"
        else:
            return app_names.pop()

    def active(self):
        return bool(self.active_sink_inputs)

    def identity(self):
        return (self.__class__.__name__, self.name())

    def may_replace_app(self, other):
        return self.identity() == other.identity()

    def wants_sink_input(self, pa_sink_input):
        # This implementation will group sink inputs for classes that
        # state that they handle them. For this fallback class, every
        # sink will get its own application instance.
        return self.handles(pa_sink_input=pa_sink_input)

    def add_sink_input(self, pa_sink_input):
        self.pa_sink_inputs.append(pa_sink_input)
        self.active_sink_inputs[pa_sink_input.index] = pa_sink_input

    def remove_sink_input_index(self, index):
        try:
            del self.active_sink_inputs[index]
        except KeyError:
            LOG.exception("remove_sink_input_index")

    def set_volume(self, *, volume, pulse):
        for si in self.active_sink_inputs.values():
            pulse.volume_set_all_chans(si, volume)

    def __repr__(self):
        indices = ", ".join(f"#{si.index}" for si in self.pa_sink_inputs)
        return f"<{self.__class__.__name__} {indices} {self.name()}>"


class Firefox(Application):
    # Firefox creates multiple sink inputs with name "AudioStream",
    # and it is difficult to distinguish them.
    @classmethod
    def handles(cls, *, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "Firefox"


class Rhythmbox(Application):
    # Rhythmbox creates a new sink input with a new media name each
    # time it plays a song.
    @classmethod
    def handles(cls, *, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "Rhythmbox"


class Spotify(Application):
    # The application.name of Spotify isn't capitalised.
    @classmethod
    def handles(cls, *, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "spotify"

    def name(self):
        return "Spotify"


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

    def add_sink_input(self, sink_input):
        for app in self.app_list:
            if app.wants_sink_input(sink_input):
                LOG.debug("Adding sink input to %r", app)
                app.add_sink_input(sink_input)
                self.app_by_index[sink_input.index] = app
                return False

        new_app = Application.get(pa_sink_input=sink_input)
        LOG.debug("Found app %r", new_app)
        self.app_by_index[sink_input.index] = new_app

        # Replace similar app
        for n, app in enumerate(self.app_list):
            if not app.active() and new_app.may_replace_app(app):
                self.app_list[n] = new_app
                return False

        # Take position of removed app if we are full
        if len(self.app_list) > 8:
            for n, app in enumerate(self.app_list):
                if not app.active:
                    self.app_list[n] = new_app
                    return True

        self.app_list.append(new_app)
        return True

    def remove_sink_input_index(self, index):
        app = self.app_by_index.pop(index)
        app.remove_sink_input_index(index)
        if app.active():
            return False
        else:
            LOG.debug("Lost app %r", app)
            return True

    def update_sink_inputs(self):
        with self.lock:
            changed = False
            sink_input_indices = set()

            for si in self.pulse.sink_input_list():
                sink_input_indices.add(si.index)

            removed_indices = set(self.app_by_index).difference(sink_input_indices)
            for index in removed_indices:
                if self.remove_sink_input_index(index):
                    changed = True

            for si in self.pulse.sink_input_list():
                if si.index not in self.app_by_index:
                    self.add_sink_input(si)
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
                app_instance = self.app_list[app]
            except IndexError:
                return

            if app_instance.active:
                app_instance.set_volume(volume=volume, pulse=self.pulse)
