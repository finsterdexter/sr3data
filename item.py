class Item:

	def __init__(self, name, type_id, category_tree):
		self.name = name
		self.type_id = type_id
		self.category_tree = category_tree

	def __str__(self):
		return f"{self.category_tree} : {self.name} : {self.type_id}"

	def __setitem__(self, key, value):
		self.__dict__[key] = value