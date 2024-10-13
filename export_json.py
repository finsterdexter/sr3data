import json
import re
from book import Book
from item import Item

import parse_functions
from skill import Skill, SkillSpecialization

all_skills = []
with open("data/SKILLS.DAT") as skills_file:
	current_skill = Skill("", "", "", "", "", "", [])
	current_class = ""
	for line in skills_file:
		line = line.rstrip()
		if (line.startswith("!")): # comment
			continue

		# skill class
		if (line.startswith("#")):
			current_class = line.lstrip("#")
			continue

		# this is a specialization
		if (line.startswith(" ")):
			if (line.endswith("->")):
				current_skill.specializations.append(SkillSpecialization.custom_specialization(line.lstrip()))
				continue
			splits = re.split(r"[\|\.]", line.lstrip())
			spec = SkillSpecialization(splits[0], splits[1], splits[2], splits[3])
			current_skill.specializations.append(spec)
			continue

		# this is a new base skill, so write the previous one
		if (current_skill.name != ""):
			all_skills.append(current_skill)

		splits = re.split(r"\)\||[\|\(\)\.]", line)
		current_skill = Skill(current_class, splits[0], splits[1], splits[2], splits[3], splits[4], [])

	all_skills.append(current_skill)

with open("output/skills.json", "w", encoding="utf-8") as output:
	# yaml.dump(all_skills, output, tags=None)
	json.dump(all_skills, output, indent=2, default=lambda o: o.__dict__)
	# print(junk)


all_adept_powers = parse_functions.parse_file("data/adept.dat")
with open("output/adept_powers.json", "w", encoding="utf-8") as output:
	json.dump(all_adept_powers, output, indent=2, default=lambda o: o.__dict__)


all_bioware = parse_functions.parse_file("data/bioware.dat")
with open("output/bioware.json", "w", encoding="utf-8") as output:
	json.dump(all_bioware, output, indent=2, default=lambda o: o.__dict__)

all_books = []
with open("data/books.dat") as books_file:
	for line in books_file:
		line = line.rstrip()
		if (line.startswith("!")): # comment
			continue

		splits = re.split(r";", line)
		load_by_default = False
		if line.startswith("*"):
			load_by_default = True
			splits[0] = splits[0].lstrip("*")
		all_books.append(Book(splits[0], splits[1], load_by_default))

with open("output/books.json", "w", encoding="utf-8") as output:
	json.dump(all_books, output, indent=2, default=lambda o: o.__dict__)


all_contacts = []
with open("data/contacts.dat") as contacts_file:
	types = dict()
	category_tree = list()
	current_category_level = 0
	
	for line in contacts_file:
		line = line.rstrip()
		if (line.startswith("!")): # comment
			continue
		
		# this is a type definition
		if (line.startswith("0-")):
			this_type = parse_functions.type_dict(line)
			types[this_type["type_id"]] = this_type
			continue

		try:
			line.index("*")
		except ValueError:
			# this is a category tree branch
			(category_tree, current_category_level) = parse_functions.parse_category_tree_branch(line, category_tree, current_category_level)
			continue

		# process an actual object
		splits = re.split(r"\*", line)
		item = Item(splits[1].strip(), 1, category_tree)
		all_contacts.append(item)

with open("output/contacts.json", "w", encoding="utf-8") as output:
	json.dump(all_contacts, output, indent=2, default=lambda o: o.__dict__)


all_critter_powers = parse_functions.parse_file("data/CPowers.dat", no_category=True)
with open("output/critter_powers.json", "w", encoding="utf-8") as output:
	json.dump(all_critter_powers, output, indent=2, default=lambda o: o.__dict__)

all_critter_weaknesses = parse_functions.parse_file("data/CWeak.dat", no_category=True)
with open("output/critter_weaknesses.json", "w", encoding="utf-8") as output:
	json.dump(all_critter_weaknesses, output, indent=2, default=lambda o: o.__dict__)

all_cyberware = parse_functions.parse_file("data/cyber.dat")
with open("output/cyberware.json", "w", encoding="utf-8") as output:
	json.dump(all_cyberware, output, indent=2, default=lambda o: o.__dict__)

all_decks = parse_functions.parse_file("data/DECK.dat")
with open("output/decks.json", "w", encoding="utf-8") as output:
	json.dump(all_decks, output, indent=2, default=lambda o: o.__dict__)

all_edges_flaws = parse_functions.parse_file("data/EDGE.DAT")
with open("output/edges_flaws.json", "w", encoding="utf-8") as output:
	json.dump(all_edges_flaws, output, indent=2, default=lambda o: o.__dict__)

all_gear = parse_functions.parse_file("data/GEAR.DAT")
with open("output/gear.json", "w", encoding="utf-8") as output:
	json.dump(all_gear, output, indent=2, default=lambda o: o.__dict__)

all_mage_gear = parse_functions.parse_file("data/MAGEGEAR.DAT")
with open("output/magegear.json", "w", encoding="utf-8") as output:
	json.dump(all_mage_gear, output, indent=2, default=lambda o: o.__dict__)

all_spells = parse_functions.parse_file("data/SPELLS.DAT")
with open("output/spells.json", "w", encoding="utf-8") as output:
	json.dump(all_spells, output, indent=2, default=lambda o: o.__dict__)

all_vehicles = parse_functions.parse_file("data/vehicles.dat")
with open("output/vehicles.json", "w", encoding="utf-8") as output:
	json.dump(all_vehicles, output, indent=2, default=lambda o: o.__dict__)
