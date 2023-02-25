def type_dict(str):
	"""
	This function converts a type definition into a dictionary. Type defs typically
	look like this:
	0-1|Adept Powers|4|Book.Page|AdeptCost|Mods|Notes|
	0-1 is the type ID of 1
	Adept Powers is the type name
	4 is the number of fields
	Book.Page is the field name
	AdeptCost is the field name
	Mods is the field name
	Notes is the field name
	"""
	if (str.startswith("0-") == False):
		raise ValueError("Invalid type definition: " + str)
	str = str.rstrip("|")
	splits = str.split("|")
	type_id = int(splits[0].lstrip("0-"))
	type_name = splits[1]
	num_fields = int(splits[2])
	field_names = splits[3:]

	if (num_fields != len(field_names)):
		raise ValueError("Invalid type definition, enumerated fields don't match defined number of fields: " + str)

	obj_dict = dict()
	for i in range(len(field_names)):
		field_names[i] = field_names[i].strip()
		obj_dict[field_names[i]] = ""

	typedef = {
		"type_id": type_id,
		"type_name": type_name,
		"num_fields": num_fields,
		"field_names": field_names,
		"obj_dict": obj_dict
	}
	return typedef


def parse_category_tree_branch(line: str, category_tree: list, current_category_level: int):
	if (line.startswith("1-") and current_category_level == 0):
		# this is a category
		category_tree.append(line.lstrip("1-"))
		current_category_level += 1
		return (category_tree, current_category_level)
	elif (line.startswith("1-") and current_category_level > 0):
		# this is a new category, so write the previous one
		category_tree = []
		category_tree.append(line.lstrip("1-"))
		current_category_level = 1
		return (category_tree, current_category_level)
	elif (line.startswith("2-") and current_category_level == 1):
		# this is a subcategory
		category_tree.append(line.lstrip("2-"))
		current_category_level += 1
		return (category_tree, current_category_level)
	elif (line.startswith("2-") and current_category_level > 1):
		# this is a new subcategory, so write the previous one
		category_tree = category_tree[:1]
		category_tree.append(line.lstrip("2-"))
		current_category_level = 2
		return (category_tree, current_category_level)
	elif (line.startswith("3-") and current_category_level == 2):
		# this is a sub-subcategory
		category_tree.append(line.lstrip("3-"))
		current_category_level += 1
		return (category_tree, current_category_level)
	elif (line.startswith("3-") and current_category_level > 2):
		# this is a new sub-subcategory, so write the previous one
		category_tree = category_tree[:2]
		category_tree.append(line.lstrip("3-"))
		current_category_level = 3
		return (category_tree, current_category_level)
	raise ValueError("Invalid category tree branch: " + line)
