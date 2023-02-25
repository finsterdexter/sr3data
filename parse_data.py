import json
import re
import yaml

import parse_functions
from skill import Skill, SkillSpecialization
from adept import AdeptPower

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


all_adept_powers = []
with open("data/adept.dat") as adept_file:
	types = dict()
	category_tree = list()
	current_category_level = 0
	
	for line in adept_file:
		line = line.rstrip()
		if (line.startswith("!")): # comment
			continue
		
		# this is a type definition
		if (line.startswith("0-")):
			this_type = parse_functions.type_dict(line)
			types[this_type["type_id"]] = this_type
			continue

		try:
			line.index("|")
		except ValueError:
			# this is a category tree branch
			(category_tree, current_category_level) = parse_functions.parse_category_tree_branch(line, category_tree, current_category_level)
			continue

		# process an actual object
		splits = re.split(r"\|", line)
		if (splits[0].startswith("2-* ") == False):
			raise Exception("Unexpected line: " + line)
		name_type = splits[0].lstrip("2-* ")
		name_type_split = name_type.rsplit(None, 1)
		name = name_type_split[0]
		type_id = name_type_split[1]
		all_adept_powers.append(AdeptPower(name, splits[1], splits[2], splits[3], splits[4].strip(), category_tree))

with open("output/adept_powers.json", "w", encoding="utf-8") as output:
	json.dump(all_adept_powers, output, indent=2, default=lambda o: o.__dict__)
