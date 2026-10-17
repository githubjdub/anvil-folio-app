from ._anvil_designer import SubscribeTemplate
from anvil import *


class Subscribe(SubscribeTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

  @handle('dashboard_button', 'click')
  def dashboard_click(self, **event_args):
    set_url_hash({'page': 'portfolio'})

  @handle('profile_button', 'click')
  def profile_click(self, **event_args):
    set_url_hash({'page': 'profile'})

  @handle('subscription_button', 'click')
  def subscription_click(self, **event_args):
    set_url_hash({'page': 'subscription'})
