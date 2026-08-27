from django import template

from apps.core import phone as phone_utils

register = template.Library()


@register.filter(name="phone_display")
def phone_display(value):
    return phone_utils.display(value)


@register.filter(name="phone_mask")
def phone_mask(value):
    return phone_utils.mask(value)


@register.filter(name="whatsapp")
def whatsapp(value, text=""):
    return phone_utils.whatsapp_link(value, text)
