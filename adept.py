class AdeptPower:
	"""
	!ADEPT.DAT - Physical Adepts powers data file - Stephen Atkins 960201
	! modified by mcmackie for SR3, Kevin Rose for Notes (thanks!), ArchangelGabriel for SOTA2064 (thanks!)
	0-1|Adept Powers|4|Book.Page|AdeptCost|Mods|Notes|
	! Powers below NOTE: *prompt for level
	"""

	def __init__(self, name, book_page, cost, mods, notes, category_tree):
		self.name = name
		self.cost = cost
		self.mods = mods
		self.notes = notes
		self.category_tree = category_tree

		bookpage_split = book_page.split(".")
		self.book = bookpage_split[0]
		self.page = bookpage_split[1]