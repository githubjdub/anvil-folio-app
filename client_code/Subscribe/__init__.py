from ._anvil_designer import SubscribeTemplate


class Subscribe(SubscribeTemplate):
  def __init__(self, **properties):
    super().__init__(**properties)

