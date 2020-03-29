"""PulseAudio sink-input interface."""

import pulsectl


class Applications:
    def __init__(self, *, controller):
        self.controller = controller
        controller.set_applications(self)
        self.pulse = pulsectl.Pulse("pafaders")

    def __enter__(self):
        self.pulse.__enter__()
        return self

    def __exit__(self, *args):
        return self.pulse.__exit__(*args)

    def set_volume(self, *, app, volume):
        try:
            app = self.pulse.sink_input_list()[app]
        except IndexError:
            return

        self.pulse.volume_set_all_chans(app, volume)
