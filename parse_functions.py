import copy
import re

from item import Item

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
	for i in range(8):
		if (line.startswith(str(i+1) + "-") and current_category_level == i):
			# this is a new subcategory
			category_tree.append(line.lstrip(str(i+1) + "-"))
			current_category_level += 1
			return (category_tree, current_category_level)
		elif (line.startswith(str(i+1) + "-") and current_category_level > i):
			# this is a new sub-subcategory, so write the previous one
			category_tree = category_tree[:i]
			category_tree.append(line.lstrip(str(i+1) + "-"))
			current_category_level = i + 1
			return (category_tree, current_category_level)
	raise ValueError("Invalid category tree branch: " + line)


def parse_file(dat_file_path: str, no_category: bool = False):
	all_items = []
	with open(dat_file_path, errors='replace') as filep:
		types = dict()
		category_tree = list()
		current_category_level = 0
		
		for line in filep:
			line = line.rstrip()
			if (line.startswith("!")): # comment
				continue
			
			# this is a type definition
			if (line.startswith("0-")):
				this_type = type_dict(line)
				types[this_type["type_id"]] = this_type
				continue

			try:
				line.index("|")
			except ValueError:
				# this is a category tree branch
				(category_tree, current_category_level) = parse_category_tree_branch(line, category_tree, current_category_level)
				continue

			# process an actual object
			splits = re.split(r"\|", line)
			if no_category == True:
				item_category_match = re.match(r"(.+?)\s+(\d+)$", splits[0])
				if (item_category_match == None):
					raise Exception("Unexpected line: " + line)
				category_level = 0
				name = item_category_match.group(1)
				type_id = item_category_match.group(2)
			else:
				item_category_match = re.match(r"(\d)-\* (.+?)\s*(\d+)$", splits[0])
				if (item_category_match == None):
					raise Exception("Unexpected line: " + line)
				category_level = item_category_match.group(1)
				name = item_category_match.group(2)
				type_id = item_category_match.group(3)
			item_type = types[int(type_id)]
			this_item = Item(name, type_id, category_tree[:int(category_level)-1])
			for i in range(item_type["num_fields"]):
				this_item[item_type["field_names"][i]] = splits[i+1].strip()
			all_items.append(this_item)
	return all_items


def parse_totems(dat_file_path: str):
	"""
	Parse TOTEMS.DAT. Format:
	  ! ...              comment
	  \\ CATEGORY NAME   category header (e.g. BASIC, TOTEM, NATURE, ...)
	  * Name|Book.Page|Type
	  <P>ENV: ...</P>    optional paragraphs, may span multiple lines
	  <P>DESC: ...</P>
	  <P>ADVAN: ...</P>
	  <P>DISADVAN: ...</P>
	  <P>free-form</P>   anything unlabeled lands in notes
	"""
	label_key = {
		"ENV": "environment",
		"DESC": "description",
		"ADVAN": "advantages",
		"DISADVAN": "disadvantages",
	}
	label_re = re.compile(r"^(ENV|DESC|ADVAN|DISADVAN)\s*:\s*", re.IGNORECASE)
	open_re = re.compile(r"<[Pp]>")
	close_re = re.compile(r"</[Pp]>")
	tag_strip_re = re.compile(r"</?[Pp]>")

	def new_totem(name, book_page, totem_type, category):
		book, _, page = book_page.partition(".")
		return {
			"name": name,
			"category": category,
			"book": book.strip(),
			"page": page.strip(),
			"type": totem_type.strip(),
			"environment": "",
			"description": "",
			"advantages": "",
			"disadvantages": "",
			"notes": "",
		}

	def flush_paragraph(totem, buf):
		text = buf.strip()
		if not text:
			return
		m = label_re.match(text)
		if m:
			key = label_key[m.group(1).upper()]
			body = text[m.end():].strip()
		else:
			key = "notes"
			body = text
		if totem[key]:
			totem[key] += "\n" + body
		else:
			totem[key] = body

	totems = []
	current_category = None
	current = None
	paragraph_buf = None

	with open(dat_file_path, errors='replace') as filep:
		for raw in filep:
			line = raw.rstrip()
			if not line or line.startswith("!"):
				continue
			if line.startswith("\\"):
				if current is not None:
					if paragraph_buf is not None:
						flush_paragraph(current, paragraph_buf)
						paragraph_buf = None
					totems.append(current)
					current = None
				current_category = line.lstrip("\\").strip()
				continue
			if line.startswith("*"):
				if current is not None:
					if paragraph_buf is not None:
						flush_paragraph(current, paragraph_buf)
						paragraph_buf = None
					totems.append(current)
				parts = line[1:].split("|")
				name = parts[0].strip()
				book_page = parts[1].strip() if len(parts) > 1 else ""
				totem_type = parts[2].strip() if len(parts) > 2 else ""
				totem_type = tag_strip_re.sub("", totem_type).strip()
				current = new_totem(name, book_page, totem_type, current_category)
				continue
			if current is None:
				continue

			remaining = line
			while remaining:
				if paragraph_buf is None:
					m = open_re.search(remaining)
					if m is None:
						break
					paragraph_buf = ""
					remaining = remaining[m.end():]
				else:
					m = close_re.search(remaining)
					if m is None:
						chunk = remaining.strip()
						if chunk:
							paragraph_buf += (" " if paragraph_buf else "") + chunk
						remaining = ""
					else:
						chunk = remaining[:m.start()].strip()
						if chunk:
							paragraph_buf += (" " if paragraph_buf else "") + chunk
						flush_paragraph(current, paragraph_buf)
						paragraph_buf = None
						remaining = remaining[m.end():]

	if current is not None:
		if paragraph_buf is not None:
			flush_paragraph(current, paragraph_buf)
		totems.append(current)
	return totems