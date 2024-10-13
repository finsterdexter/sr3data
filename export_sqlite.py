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
        'data_file': 'data/CPowers.dat',
        'columns': ['name', 'Notes', 'BookPage', 'Type', 'Action', 'Range', 'Duration', 'type_id']
    },
    'critter_weaknesses': {
        'data_file': 'data/CWeak.dat',
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
    'gear': {
        'data_file': 'data/GEAR.DAT',
        'columns': ['name', 'Notes', 'BookPage', 'category_tree', 'Availability', '$Cost', 'Legal', 'Street Index', 'type_id', 'Concealability', 'Reach', 'Damage', 'Weight']
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
        'columns': ['name', 'type_id', 'category_tree', 'Handling', 'Speed', 'Body', 'Armor', 'Sig', 'Apilot', 'Availability', '$Cost', 'Street Index', 'BookPage', 'Speed/Accel', 'Body/Armor', 'Sig/Autonav', 'Pilot/Sensor', 'Cargo/Load', 'Seating', 'Notes']
    }
}

# Process each table
for table_name, info in tables_info.items():
    process_items_table(table_name, info['data_file'], info['columns'])

# Processing contacts separately due to custom parsing
cursor.execute('''
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        type_id INTEGER,
        category TEXT
    )
''')

with open("data/contacts.dat") as contacts_file:
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

# Close the database connection
conn.close()
