import json
import re
import sqlite3
from book import Book
from item import Item

import parse_functions
from skill import Skill, SkillSpecialization

# Connect to the SQLite database (it will be created if it doesn't exist)
conn = sqlite3.connect('output/data.db')
cursor = conn.cursor()

# Create tables for skills and specializations
cursor.execute('''
    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_class TEXT,
        name TEXT,
        atr TEXT,
        book TEXT,
        page TEXT,
        notes TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS skill_specializations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id INTEGER,
        name TEXT,
        book TEXT,
        page TEXT,
        notes TEXT,
        FOREIGN KEY(skill_id) REFERENCES skills(id)
    )
''')

# Create table for books
cursor.execute('''
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        abbreviation TEXT,
        load_as_default BOOLEAN
    )
''')

# Processing SKILLS.DAT
with open("data/SKILLS.DAT") as skills_file:
    current_skill = None
    current_class = ""
    for line in skills_file:
        line = line.rstrip()
        if line.startswith("!"):  # comment
            continue

        # Skill class
        if line.startswith("#"):
            current_class = line.lstrip("#")
            continue

        # Specialization
        if line.startswith(" "):
            if current_skill is None:
                continue  # No base skill to attach to
            if line.endswith("->"):
                spec = SkillSpecialization.custom_specialization(line.lstrip())
                current_skill.specializations.append(spec)
                continue
            splits = re.split(r"[\|\.]", line.lstrip())
            spec = SkillSpecialization(splits[0], splits[1], splits[2], splits[3])
            current_skill.specializations.append(spec)
            continue

        # New base skill; insert the previous one
        if current_skill is not None:
            # Insert current_skill into the database
            cursor.execute('''
                INSERT INTO skills (skill_class, name, atr, book, page, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (current_skill.skill_class, current_skill.name, current_skill.atr,
                  current_skill.book, current_skill.page, current_skill.notes))
            current_skill_id = cursor.lastrowid

            # Insert specializations
            for spec in current_skill.specializations:
                cursor.execute('''
                    INSERT INTO skill_specializations (skill_id, name, book, page, notes)
                    VALUES (?, ?, ?, ?, ?)
                ''', (current_skill_id, spec.name, spec.book, spec.page, spec.notes))

        splits = re.split(r"\)\||[\|\(\)\.]", line)
        current_skill = Skill(current_class, splits[0], splits[1], splits[2], splits[3], splits[4], [])

    # Insert the last skill
    if current_skill is not None:
        cursor.execute('''
            INSERT INTO skills (skill_class, name, atr, book, page, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (current_skill.skill_class, current_skill.name, current_skill.atr,
              current_skill.book, current_skill.page, current_skill.notes))
        current_skill_id = cursor.lastrowid

        for spec in current_skill.specializations:
            cursor.execute('''
                INSERT INTO skill_specializations (skill_id, name, book, page, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (current_skill_id, spec.name, spec.book, spec.page, spec.notes))

conn.commit()

# Processing books.dat
with open("data/books.dat") as books_file:
    for line in books_file:
        line = line.rstrip()
        if line.startswith("!"):  # comment
            continue

        splits = re.split(r";", line)
        load_by_default = False
        if line.startswith("*"):
            load_by_default = True
            splits[0] = splits[0].lstrip("*")
        book = Book(splits[0], splits[1], load_by_default)

        # Insert into database
        cursor.execute('''
            INSERT INTO books (name, abbreviation, load_as_default)
            VALUES (?, ?, ?)
        ''', (book.name, book.abbreviation, book.load_as_default))

conn.commit()

# Function to create a table and insert items for a given data file
def process_items_table(table_name, data_file, columns):
    # Create table with dynamic columns
    columns_sql = ", ".join([col.replace(" ", "").replace("/", "").replace("$", "") for col in columns])
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {columns_sql}
        )
    ''')

    # Parse items from data file
    if (table_name == "critter_powers" or table_name == "critter_weaknesses"):
        items = parse_functions.parse_file(data_file, True)
    else:
        items = parse_functions.parse_file(data_file)

    # Insert items into the table
    for item in items:
        values = []
        for col in columns:
            if (col == "BookPage"):
                value = getattr(item, "Book.Page", None)
            elif (col == "category_tree"):
                value = " > ".join(item.category_tree) if hasattr(item, 'category_tree') else None
            else:
                value = getattr(item, col, None)                
            values.append(value)
        placeholders = ", ".join(["?"] * len(columns))
        sql_format = f'''
            INSERT INTO {table_name} ({", ".join([col.replace(" ", "").replace("/", "").replace("$", "") for col in columns])})
            VALUES ({placeholders})
        '''
        cursor.execute(sql_format, values)
    conn.commit()

# Define the columns for each table based on assumed attributes
tables_info = {
    'adept_powers': {
        'data_file': 'data/adept.dat',
        'columns': ['name', 'AdeptCost', 'Notes', 'category_tree', 'BookPage', 'Mods', 'type_id']
    },
    'bioware': {
        'data_file': 'data/bioware.dat',
        'columns': ['name', 'BioIndex', 'Availability', '$Cost', 'Notes', 'category_tree', 'BookPage', 'Mods', 'Street Index', 'Type', 'type_id']
    },
    'critter_powers': {
        'data_file': 'data/CPowers.DAT',
        'columns': ['name', 'Notes', 'BookPage', 'Type', 'Action', 'Range', 'Duration', 'type_id']
    },
    'critter_weaknesses': {
        'data_file': 'data/CWeak.DAT',
        'columns': ['name', 'Notes', 'BookPage', 'type_id']
    },
    'cyberware': {
        'data_file': 'data/cyber.dat',
        'columns': ['name', 'Notes', 'BookPage', 'category_tree', 'Availability', 'EssCost', '$Cost', 'Mods', 'LegalCode', 'Capacity', 'Category', 'Street Index', 'type_id']
    },
    'decks': {
        'data_file': 'data/DECK.dat',
        'columns': ['name', 'BookPage', 'category_tree', 'type_id', 'Availability', '$Cost', 'Street Index', 'Persona', 'Hardening', 'Memory', 'Storage', 'I/O Speed', 'Response Increase']
    },
    'edges_flaws': {
        'data_file': 'data/EDGE.DAT',
        'columns': ['name', 'Notes', 'BookPage', 'type_id', 'category_tree', '$Cost', 'EorF', 'Mods']
    },
    'magegear': {
        'data_file': 'data/MAGEGEAR.DAT',
        'columns': ['name', 'type_id', 'category_tree', 'KarmaCost', 'Availability', '$Cost', 'Street Index', 'BookPage']
    },
    'spells': {
        'data_file': 'data/SPELLS.DAT',
        'columns': ['name', 'type_id', 'category_tree', 'BookPage', 'Type', 'Target', 'Range', 'Duration', 'Drain', 'Class', 'Notes']
    },
    'vehicles': {
        'data_file': 'data/vehicles.dat',
        # The vehicles table is a catch-all for three .dat type families:
        #   17 (SR2 Cars): Handling, Speed, Body, Armor, Sig, Apilot, ...
        #   24 (R3 Cars2): Handling, Speed/Accel, Body/Armor, Sig/Autonav,
        #                  Pilot/Sensor, Cargo/Load, Seating, ...
        #   23 (Vehicle modifications): Equipment, Base Time/Skill Test, CF, Load, Notes, ...
        # Each row populates whichever subset of columns its type defines;
        # the rest stay NULL. The export rule "no values dropped" means we
        # list every union member here.
        'columns': ['name', 'type_id', 'category_tree', 'Handling', 'Speed', 'Body', 'Armor', 'Sig', 'Apilot', 'Availability', '$Cost', 'Street Index', 'BookPage', 'Speed/Accel', 'Body/Armor', 'Sig/Autonav', 'Pilot/Sensor', 'Cargo/Load', 'Seating', 'Notes', 'Equipment', 'Base Time/Skill Test', 'CF', 'Load']
    }
}

# Process each table
for table_name, info in tables_info.items():
    process_items_table(table_name, info['data_file'], info['columns'])

# Overlay R3 rules data onto vehicle gear rows. The vehicles table now also
# carries cf_consumed / load_kg_formula / mount_points / engine_track columns
# for mods (rows under "Vehicle Gear > ..."). Authoring lives in the
# vehicle_gear_rules.json file alongside the .DAT inputs; this script reads
# the JSON and runs UPDATE statements matching on (category_tree, name).
cursor.execute("ALTER TABLE vehicles ADD COLUMN cf_consumed REAL")
cursor.execute("ALTER TABLE vehicles ADD COLUMN load_kg_formula TEXT")
cursor.execute("ALTER TABLE vehicles ADD COLUMN mount_points INTEGER")
cursor.execute("ALTER TABLE vehicles ADD COLUMN engine_track TEXT")

# Parsed pair-split columns. Every paired raw field (Handling="3/4",
# SpeedAccel="220/13", BodyArmor="2/2", etc.) gets split into two TEXT
# columns holding the literal halves on either side of the top-level '/'.
# The split is paren-aware so values like "(7(7)/6(4))/4" don't get
# misclassified. Single-value rows ("special", "5", "-") leave the parsed
# columns NULL — the raw column is the source of truth there. Multi-mode
# rows like "(4)/(4/8)/(4)" (three top-level groups) likewise leave the
# parsed columns NULL; the UI falls back to the raw text. No int conversion
# happens here — the parsed columns are TEXT to preserve every weird half
# ("-", "75(105)", etc.) without coercion.
parsed_pair_columns = [
    ("handling_on",      "handling_off",      "Handling"),
    ("speed_cruise_sr2", "speed_max_sr2",     "Speed"),
    ("speed_r3",         "acceleration_r3",   "SpeedAccel"),
    ("body_r3",          "armor_r3",          "BodyArmor"),
    ("sig_r3",           "autonav_r3",        "SigAutonav"),
    ("pilot_r3",         "sensor_r3",         "PilotSensor"),
    ("cargo_r3",         "load_r3",           "CargoLoad"),
]
for left, right, _ in parsed_pair_columns:
    cursor.execute(f"ALTER TABLE vehicles ADD COLUMN {left} TEXT")
    cursor.execute(f"ALTER TABLE vehicles ADD COLUMN {right} TEXT")


def split_pair_paren_aware(raw):
    """Split `raw` on the first top-level '/' (depth 0 outside parens).
    Returns (left, right) when exactly one top-level '/' is found, else
    (None, None). Multi-mode values like "(4)/(4/8)/(4)" have three
    top-level groups and are intentionally not split (per UI fallback rule)."""
    if raw is None:
        return (None, None)
    parts = []
    current = []
    depth = 0
    for ch in raw:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == '/' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current))
    if len(parts) != 2:
        return (None, None)
    return (parts[0], parts[1])


# Walk every vehicle row once and populate the parsed pair columns.
rows = cursor.execute("SELECT id, Handling, Speed, SpeedAccel, BodyArmor, "
                      "SigAutonav, PilotSensor, CargoLoad FROM vehicles").fetchall()
for row in rows:
    row_id = row[0]
    raw_values = {
        "Handling":    row[1],
        "Speed":       row[2],
        "SpeedAccel":  row[3],
        "BodyArmor":   row[4],
        "SigAutonav":  row[5],
        "PilotSensor": row[6],
        "CargoLoad":   row[7],
    }
    sets = []
    args = []
    for left, right, source in parsed_pair_columns:
        l, r = split_pair_paren_aware(raw_values[source])
        if l is not None:
            sets.append(f"{left} = ?")
            sets.append(f"{right} = ?")
            args.extend([l, r])
    if sets:
        args.append(row_id)
        cursor.execute(
            f"UPDATE vehicles SET {', '.join(sets)} WHERE id = ?",
            args,
        )
conn.commit()

with open("data/vehicle_gear_rules.json") as rules_file:
    rules_doc = json.load(rules_file)

unmatched = []
for entry in rules_doc.get("entries", []):
    result = cursor.execute(
        "UPDATE vehicles SET cf_consumed = ?, load_kg_formula = ?, "
        "mount_points = ?, engine_track = ? "
        "WHERE category_tree = ? AND name = ?",
        (
            entry.get("cf_consumed"),
            entry.get("load_kg_formula"),
            entry.get("mount_points"),
            entry.get("engine_track"),
            entry["category_tree"],
            entry["name"],
        ),
    )
    if cursor.rowcount == 0:
        unmatched.append((entry["category_tree"], entry["name"]))

if unmatched:
    print(f"WARNING: {len(unmatched)} vehicle_gear_rules entries did not match any row:")
    for ct, n in unmatched:
        print(f"  {ct!r} / {n!r}")

conn.commit()

# Processing GEAR.DAT with parent/child table structure
# Parent table: gear (common fields)
# Child tables: gear_melee, gear_ranged, gear_armor, gear_accessories,
#               gear_electronics, gear_chemicals, gear_rated, gear_fireforce

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        type_id INTEGER,
        category_tree TEXT,
        concealability TEXT,
        weight TEXT,
        cost TEXT,
        availability TEXT,
        street_index TEXT,
        book_page TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_melee (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        reach TEXT,
        damage TEXT,
        legal TEXT,
        notes TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_ranged (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        str_min TEXT,
        ammunition TEXT,
        mode TEXT,
        damage TEXT,
        accessories TEXT,
        intelligence TEXT,
        blast TEXT,
        scatter TEXT,
        legal TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_armor (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        ballistic TEXT,
        impact TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_accessories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        mount TEXT,
        rating TEXT,
        notes TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_electronics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        mag TEXT,
        type TEXT,
        rating TEXT,
        memory TEXT,
        form TEXT,
        eccm TEXT,
        data_encrypt TEXT,
        comm_encrypt TEXT,
        legal TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_chemicals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        addiction TEXT,
        tolerance TEXT,
        edge TEXT,
        origin TEXT,
        speed TEXT,
        vector TEXT,
        damage TEXT,
        rating TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_rated (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        rating TEXT,
        type TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS gear_fireforce (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gear_id INTEGER,
        points TEXT,
        points_used TEXT,
        notes TEXT,
        FOREIGN KEY(gear_id) REFERENCES gear(id)
    )
''')

# Map type_id to child table and field mappings
gear_child_tables = {
    # gear_melee: type 1
    1: ('gear_melee', {'Reach': 'reach', 'Damage': 'damage', 'Legal': 'legal', 'Notes': 'notes'}),
    # gear_ranged: types 2, 3, 4, 5, 27
    2: ('gear_ranged', {'Str.Min.': 'str_min', 'Damage': 'damage', 'Legal': 'legal'}),
    3: ('gear_ranged', {'Ammunition': 'ammunition', 'Mode': 'mode', 'Damage': 'damage', 'Accessories': 'accessories'}),
    4: ('gear_ranged', {'Intelligence': 'intelligence', 'Damage': 'damage', 'Blast': 'blast', 'Scatter': 'scatter'}),
    5: ('gear_ranged', {'Damage': 'damage'}),
    27: ('gear_ranged', {'Intelligence': 'intelligence', 'Accessories': 'accessories'}),
    # gear_armor: type 8
    8: ('gear_armor', {'Ballistic': 'ballistic', 'Impact': 'impact'}),
    # gear_accessories: type 6
    6: ('gear_accessories', {'Mount': 'mount', 'Rating': 'rating', 'Notes': 'notes'}),
    # gear_electronics: types 9, 20, 30
    9: ('gear_electronics', {'Mag:': 'mag'}),
    20: ('gear_electronics', {'Type': 'type', 'Rating': 'rating', 'Memory': 'memory'}),
    30: ('gear_electronics', {'Form': 'form', 'Memory': 'memory', 'ECCM': 'eccm', 'Data Encrypt': 'data_encrypt', 'Comm Encrypt': 'comm_encrypt', 'Legal': 'legal'}),
    # gear_chemicals: types 22, 29, 34
    22: ('gear_chemicals', {'Addiction': 'addiction', 'Tolerance': 'tolerance', 'Edge': 'edge'}),
    29: ('gear_chemicals', {'Origin': 'origin', 'Rating': 'rating', 'Speed': 'speed', 'Vector': 'vector'}),
    34: ('gear_chemicals', {'Damage': 'damage', 'Vector': 'vector', 'Speed': 'speed'}),
    # gear_rated: types 7, 13, 15, 21, 35, 36, 38
    7: ('gear_rated', {'Rating': 'rating'}),
    13: ('gear_rated', {'Rating': 'rating'}),
    15: ('gear_rated', {}),  # magical equipment - no unique fields after dropping karma_cost
    21: ('gear_rated', {'Rating': 'rating'}),
    35: ('gear_rated', {'Type': 'type', 'Rating': 'rating'}),
    36: ('gear_rated', {'Rating': 'rating'}),
    38: ('gear_rated', {'Rating': 'rating'}),
    # gear_fireforce: types 31, 32
    31: ('gear_fireforce', {'Points': 'points', 'Notes': 'notes'}),
    32: ('gear_fireforce', {'Points Used': 'points_used', 'Notes': 'notes'}),
    # No child table needed: types 10, 11, 14, 26, 28
}

# Parse gear items
all_gear = parse_functions.parse_file("data/GEAR.DAT")

for item in all_gear:
    # Insert into parent gear table
    cursor.execute('''
        INSERT INTO gear (name, type_id, category_tree, concealability, weight, cost, availability, street_index, book_page)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        item.name,
        item.type_id,
        " > ".join(item.category_tree) if hasattr(item, 'category_tree') else None,
        getattr(item, 'Concealability', None),
        getattr(item, 'Weight', None),
        getattr(item, '$Cost', None),
        getattr(item, 'Availability', None),
        getattr(item, 'Street Index', None),
        getattr(item, 'Book.Page', None)
    ))
    gear_id = cursor.lastrowid

    # Insert into child table if applicable
    type_id = int(item.type_id)
    if type_id in gear_child_tables:
        child_table, field_map = gear_child_tables[type_id]
        if field_map:  # Only insert if there are fields to map
            columns = ['gear_id'] + list(field_map.values())
            values = [gear_id] + [getattr(item, src_field, None) for src_field in field_map.keys()]
            placeholders = ', '.join(['?'] * len(values))
            cursor.execute(f'''
                INSERT INTO {child_table} ({', '.join(columns)})
                VALUES ({placeholders})
            ''', values)

conn.commit()

# Processing contacts separately due to custom parsing
cursor.execute('''
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        type_id INTEGER,
        category TEXT
    )
''')

with open("data/contacts.dat", errors='replace') as contacts_file:
    types = dict()
    category_tree = list()
    current_category_level = 0
    
    for line in contacts_file:
        line = line.rstrip()
        if line.startswith("!"):  # comment
            continue
        
        # Type definition
        if line.startswith("0-"):
            this_type = parse_functions.type_dict(line)
            types[this_type["type_id"]] = this_type
            continue

        try:
            line.index("*")
        except ValueError:
            # Category tree branch
            (category_tree, current_category_level) = parse_functions.parse_category_tree_branch(
                line, category_tree, current_category_level)
            continue

        # Process actual object
        splits = re.split(r"\*", line)
        item = Item(splits[1].strip(), 1, category_tree)

        # Insert into database
        cursor.execute('''
            INSERT INTO contacts (name, type_id, category)
            VALUES (?, ?, ?)
        ''', (item.name, item.type_id, " > ".join(item.category_tree)))

conn.commit()

# Processing TOTEMS.DAT
cursor.execute('''
    CREATE TABLE IF NOT EXISTS totems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        category TEXT,
        book TEXT,
        page TEXT,
        type TEXT,
        environment TEXT,
        description TEXT,
        advantages TEXT,
        disadvantages TEXT,
        notes TEXT
    )
''')

for totem in parse_functions.parse_totems("data/TOTEMS.DAT"):
    cursor.execute('''
        INSERT INTO totems (name, category, book, page, type, environment,
                            description, advantages, disadvantages, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        totem["name"], totem["category"], totem["book"], totem["page"],
        totem["type"], totem["environment"], totem["description"],
        totem["advantages"], totem["disadvantages"], totem["notes"],
    ))

conn.commit()

# Rules glossary — hand-authored explanatory text shown as in-app tooltips
# next to rules-bound widgets (spell flags, skill specializations, starting
# spirits cost, etc.). Keyed by short ids the C# code looks up.
cursor.execute('''
    CREATE TABLE IF NOT EXISTS rules_glossary (
        key TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        cost_note TEXT,
        book TEXT,
        page INTEGER
    )
''')

rules_glossary_entries = [
    (
        "skill.specialization",
        "Skill Specialization",
        "A specialization narrows a base skill to a particular form, style, or "
        "subset, granting greater proficiency in that focus area. Listed in "
        "parentheses after the skill name (e.g. Pistols (Ares Predator)). "
        "At character creation, choose the specialization and give it a rating "
        "one point higher than the base skill; subtract one from the base "
        "skill rating. Each character may have only one specialization per "
        "base skill at character creation.",
        "Specialization rating = base skill + 1, base skill rating - 1",
        "SR3", 82,
    ),
    (
        "spell.fetish",
        "Fetish (-1 modifier)",
        "Casting a fetish-limited spell requires an enchanted re-use object "
        "known as a fetish. Fetishes are available from talismongers, made "
        "for a specific category of spells (combat, detection, and so on), "
        "and attuned to the magician when the spell is learned. Without the "
        "fetish on the magician's body, the limited spell cannot be cast.",
        "-1 modifier to spell Force for Drain, or to learning Karma cost",
        "SR3", 180,
    ),
    (
        "spell.exclusive",
        "Exclusive (-2 modifier)",
        "An exclusive limited spell requires more concentration than an "
        "ordinary spell, making casting and sustaining the spell an Exclusive "
        "Action. The limit reduces the spell's Force for Drain, or its "
        "learning Karma cost - the player chooses which when the spell is "
        "learned.",
        "-2 modifier to spell Force for Drain, or to learning Karma cost",
        "SR3", 180,
    ),
    (
        "spirits.starting",
        "Starting Bound Spirits",
        "A magician character may purchase starting bound spirits at character "
        "creation using Spell Points. Each point of the spirit's Force costs "
        "one Spell Point, and each owed service costs two Spell Points. The "
        "spirit's Force may not exceed the character's Magic Attribute, and "
        "the character may bind no more spirits at one time than his or her "
        "Charisma.",
        "Cost = Force + (Services x 2) Spell Points",
        "SR3", 184,
    ),
]

cursor.executemany(
    "INSERT OR REPLACE INTO rules_glossary (key, title, body, cost_note, book, page) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    rules_glossary_entries,
)
conn.commit()

# Close the database connection
conn.close()
