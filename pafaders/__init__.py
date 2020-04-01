#!/usr/bin/env python3

import logging
import time

import click

from pafaders.controller import Controller
from pafaders.midi import MidiListener
from pafaders.pulseaudio import Applications


LOG = logging.getLogger(__name__)


@click.command()
@click.option("--verbose", "-v", count=True)
def main(verbose):
    if verbose > 0:
        level = logging.DEBUG - verbose + 1
    else:
        level = logging.INFO

    logging.basicConfig(level=level)

    controller = Controller()

    with Applications(controller=controller) as apps:
        with MidiListener(controller=controller) as listener:
            try:
                while True:
                    # Periodically check for new MIDI ports
                    listener.check_ports()
                    # Periodically check for new apps
                    apps.check()
                    time.sleep(1)
            except KeyboardInterrupt:
                LOG.info("Exiting")
            except Exception:
                LOG.exception("Killed by exception")
                raise SystemExit(1)


if __name__ == "__main__":
    main()
