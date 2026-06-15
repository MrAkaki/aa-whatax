from django.apps import AppConfig

from whatax import __version__


class WhataxConfig(AppConfig):
    name = "whatax"
    label = "whatax"
    verbose_name = f"Whale Tax v{__version__}"
