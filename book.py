class Book:

	def __init__(self, name: str, abbreviation: str, load_as_default: bool):
		self.name = name
		self.abbreviation = abbreviation
		self.load_as_default = load_as_default