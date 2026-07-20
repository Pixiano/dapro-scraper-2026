"""Collector registrations. Importing this package wires real collectors into
the registry; anything unregistered falls back to the stub in base.py."""

from . import (base, github, linkedin, linktree, medium, news, patreon, reddit,
               social, substack, twitch, twitter, website, youtube)

base.register(base.WEBSITE, website.collect)
base.register(base.YOUTUBE, youtube.collect)
base.register(base.INSTAGRAM, social.collect)
base.register(base.FACEBOOK, social.collect)
base.register(base.LINKEDIN, linkedin.collect)
base.register(base.GITHUB, github.collect)
base.register(base.MEDIUM, medium.collect)
base.register(base.SUBSTACK, substack.collect)
base.register(base.REDDIT, reddit.collect)
base.register(base.TWITTER, twitter.collect)
base.register(base.NEWS, news.collect)
base.register(base.LINKTREE, linktree.collect)
base.register(base.PATREON, patreon.collect)
base.register(base.KOFI, patreon.collect)
base.register(base.TWITCH, twitch.collect)
