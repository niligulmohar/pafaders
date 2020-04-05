"""MPRIS 2 media player and PulseAudio sink input interface."""

import enum
import logging
import threading

import dbus
import mpris2
import pulsectl


LOG = logging.getLogger(__name__)


class PlaybackStatus(enum.Enum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"


class Application:
    def __init__(self, *, pa_sink_input=None, mpris_player_uri=None):
        self.pa_sink_inputs = []
        self.mpris_app = None
        self.mpris_player = None
        self.cached_mpris_identity = None
        self.active_sink_inputs = {}

        if pa_sink_input is not None:
            self.add_sink_input(pa_sink_input)

        if mpris_player_uri is not None:
            self.add_player_uri(mpris_player_uri)

    @classmethod
    def get(cls, *, pa_sink_input=None, mpris_player_uri=None):
        for subclass in cls.__subclasses__():
            if subclass.handles(
                pa_sink_input=pa_sink_input, mpris_player_uri=mpris_player_uri
            ):
                app_class = subclass
                break
        else:
            app_class = cls

        return app_class(pa_sink_input=pa_sink_input, mpris_player_uri=mpris_player_uri)

    @classmethod
    def handles_pa_sink_input(cls, pa_sink_input):
        return False

    @classmethod
    def handles_mpris_player_uri(cls, pa_sink_input):
        return False

    @classmethod
    def handles(cls, *, pa_sink_input=None, mpris_player_uri=None):
        if pa_sink_input is None and mpris_player_uri is None:
            return False

        if pa_sink_input is not None:
            return cls.handles_pa_sink_input(pa_sink_input)

        if mpris_player_uri is not None:
            return cls.handles_mpris_player_uri(mpris_player_uri)

    def mpris_identity(self):
        try:
            self.cached_mpris_identity = str(self.mpris_app.Identity)
        except dbus.exceptions.DBusException:
            pass
        return self.cached_mpris_identity

    def name(self):
        if self.mpris_app is not None:
            return self.mpris_identity()

        app_names = {si.proplist["application.name"] for si in self.pa_sink_inputs}
        media_names = {si.proplist["media.name"] for si in self.pa_sink_inputs}

        if len(app_names) == len(media_names) == 1:
            # Same convention as pavucontrol
            return f"{app_names.pop()} : {media_names.pop()}"
        else:
            return app_names.pop()

    def active(self):
        return bool(self.active_sink_inputs) or self.mpris_player is not None

    def identity(self):
        return (self.__class__.__name__, self.name())

    def may_replace_app(self, other):
        return self.identity() == other.identity()

    def wants_sink_input(self, pa_sink_input):
        # This implementation will group sink inputs for classes that
        # state that they handle them. For this fallback class, every
        # sink will get its own application instance.
        return self.handles_pa_sink_input(pa_sink_input)

    def wants_player_uri(self, player_uri):
        return self.handles_mpris_player_uri(player_uri)

    def add_sink_input(self, pa_sink_input):
        self.pa_sink_inputs.append(pa_sink_input)
        self.active_sink_inputs[pa_sink_input.index] = pa_sink_input

    def add_player_uri(self, player_uri):
        self.mpris_app = mpris2.MediaPlayer2(
            dbus_interface_info={"dbus_uri": player_uri}
        )
        self.mpris_player = mpris2.Player(dbus_interface_info={"dbus_uri": player_uri})

    def remove_sink_input_index(self, index):
        try:
            del self.active_sink_inputs[index]
        except KeyError:
            LOG.exception("remove_sink_input_index")

    def remove_player(self):
        self.mpris_app = None
        self.mpris_player = None

    def set_pa_volume(self, *, volume, pulse):
        for si in self.active_sink_inputs.values():
            pulse.volume_set_all_chans(si, volume)

    def set_mpris_volume(self, volume):
        self.mpris_player.Volume = volume

    def set_volume(self, *, volume, pulse):
        if self.mpris_player is not None:
            self.set_mpris_volume(volume)
        else:
            self.set_pa_volume(volume=volume, pulse=pulse)

    @property
    def playback_status(self):
        if self.mpris_player is None:
            return None
        else:
            try:
                return PlaybackStatus(self.mpris_player.PlaybackStatus)
            except dbus.exceptions.DBusException:
                return None

    def play_or_pause(self):
        if self.mpris_player is not None:
            self.mpris_player.PlayPause()

    def play(self):
        if self.mpris_player is not None:
            self.mpris_player.Play()

    def pause(self):
        if self.mpris_player is not None:
            self.mpris_player.Pause()

    def __repr__(self):
        indices = ", ".join(f"#{index}" for index in self.active_sink_inputs)
        return f"<{self.__class__.__name__} {self.name()} ({indices})>"


class Firefox(Application):
    # Firefox creates multiple sink inputs with name "AudioStream",
    # and it is difficult to distinguish them.
    @classmethod
    def handles_pa_sink_input(cls, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "Firefox"


class Rhythmbox(Application):
    @classmethod
    def handles_pa_sink_input(cls, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "Rhythmbox"

    @classmethod
    def handles_mpris_player_uri(cls, mpris_player_uri):
        return mpris_player_uri == "org.mpris.MediaPlayer2.rhythmbox"


class Spotify(Application):
    @classmethod
    def handles_pa_sink_input(cls, pa_sink_input):
        return pa_sink_input.proplist["application.name"] == "spotify"

    @classmethod
    def handles_mpris_player_uri(cls, mpris_player_uri):
        return mpris_player_uri == "org.mpris.MediaPlayer2.spotify"

    def set_volume(self, *, volume, pulse):
        # The media player object of Spotify does not respond to
        # volume changes.
        self.set_pa_volume(volume=volume, pulse=pulse)


class Applications:
    def __init__(self, *, controller):
        self.controller = controller
        self.controller.subscribe("set_volume", self.set_volume)
        self.controller.subscribe("play_or_pause", self.play_or_pause)
        self.pulse = pulsectl.Pulse("pafaders")
        self.app_by_sink_input_index = {}
        self.app_by_player_uri = {}
        self.app_list = []
        self.playback_status_list = []
        self.playing_app = None

        # We may be called via callback functions in other threads.
        self.lock = threading.Lock()

    def __enter__(self):
        self.pulse.__enter__()
        return self

    def __exit__(self, *args):
        return self.pulse.__exit__(*args)

    def add_app(self, new_app):
        # Replace similar app
        for n, app in enumerate(self.app_list):
            if not app.active() and app.may_replace_app(app):
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

    def add_sink_input(self, sink_input):
        for app in self.app_list:
            if app.wants_sink_input(sink_input):
                LOG.debug("Adding sink input to %r", app)
                app.add_sink_input(sink_input)
                self.app_by_sink_input_index[sink_input.index] = app
                return False

        new_app = Application.get(pa_sink_input=sink_input)
        LOG.debug("Found app %r", new_app)
        self.app_by_sink_input_index[sink_input.index] = new_app

        return self.add_app(new_app)

    def add_player_uri(self, player_uri):
        for app in self.app_list:
            if app.wants_player_uri(player_uri):
                LOG.debug("Adding player to %r", app)
                app.add_player_uri(player_uri)
                self.app_by_player_uri[player_uri] = app
                return False

        new_app = Application.get(mpris_player_uri=player_uri)
        LOG.debug("Found app %r", new_app)
        self.app_by_player_uri[player_uri] = new_app

        return self.add_app(new_app)

    def remove_sink_input_index(self, index):
        app = self.app_by_sink_input_index.pop(index)
        app.remove_sink_input_index(index)
        if app.active():
            return False
        else:
            LOG.debug("Lost app %r", app)
            return True

    def update_sink_inputs(self):
        with self.lock:
            changed = False
            sink_inputs = self.pulse.sink_input_list()
            sink_input_indices = {si.index for si in sink_inputs}

            removed_indices = set(self.app_by_sink_input_index).difference(
                sink_input_indices
            )
            for index in removed_indices:
                if self.remove_sink_input_index(index):
                    changed = True

            for si in sink_inputs:
                if si.index not in self.app_by_sink_input_index:
                    self.add_sink_input(si)
                    changed = True

            return changed

    def update_media_players(self):
        changed = False
        first_app = None
        first_playing_app = None
        uris = [str(uri) for uri in mpris2.get_players_uri()]

        removed_uris = set(self.app_by_player_uri).difference(uris)
        for uri in removed_uris:
            LOG.debug("Removed uri %r", uri)
            app = self.app_by_player_uri.pop(uri)
            app.remove_player()
            changed = True

        for uri in uris:
            if uri not in self.app_by_player_uri:
                self.add_player_uri(uri)
                changed = True
            app = self.app_by_player_uri[uri]
            if first_app is None:
                first_app = app
            if first_playing_app is None:
                if app.playback_status != PlaybackStatus.STOPPED:
                    first_playing_app = app
            else:
                if (
                    app.playback_status == PlaybackStatus.PLAYING
                    and first_playing_app.playback_status == PlaybackStatus.PAUSED
                ):
                    first_playing_app = app

        if first_playing_app is None:
            pass
        elif self.playing_app is None:
            self.playing_app = first_playing_app
            LOG.debug("Current player: %r", self.playing_app)
        elif (
            first_playing_app.playback_status == PlaybackStatus.PLAYING
            and self.playing_app.playback_status != PlaybackStatus.PLAYING
        ):
            self.playing_app = first_playing_app
            LOG.debug("Changed current player to: %r", self.playing_app)

        playback_status_list = [a.playback_status for a in self.app_list]
        if playback_status_list != self.playback_status_list:
            changed = True

        return changed

    def check(self):
        # Poll for updates. We might listen for events instead, but
        # then we cannot use blocking APIs like sink_input_list() at
        # the same time.
        changed0 = self.update_sink_inputs()

        changed1 = self.update_media_players()

        if changed0 or changed1:
            self.controller.set_application_list(self.app_list)

    def set_volume(self, *, app, volume):
        with self.lock:
            try:
                app_instance = self.app_list[app]
            except IndexError:
                return

            if app_instance.active:
                app_instance.set_volume(volume=volume, pulse=self.pulse)

    def play_or_pause(self, *, app=None):
        if app is None:
            self.playing_app.play_or_pause()
            return

        playing = self.app_list[app].playback_status == PlaybackStatus.PLAYING
        if playing:
            self.app_list[app].pause()
        else:
            for index, app_object in enumerate(self.app_list):
                if index == app:
                    app_object.play()
                else:
                    app_object.pause()
