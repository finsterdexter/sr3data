class Skill:
	"""From SKILLS.DAT:
	skill:
		class, name, atr, book, page, notes, specializations []
	"""

	def __init__(self, skill_class, name, atr, book, page, notes, specializations):
		self.skill_class = skill_class
		self.name = name
		self.atr = atr
		self.book = book
		self.page = page
		self.notes = notes
		self.specializations = specializations

class SkillSpecialization:
	"""
	name, book, page, notes
	"""

	def __init__(self, name, book = "", page = "", notes = ""):
		self.name = name
		self.book = book
		self.page = page
		self.notes = notes

	@classmethod
	def custom_specialization(cls, name):
		return cls(name)