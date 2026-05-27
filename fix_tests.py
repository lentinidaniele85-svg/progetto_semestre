import re

def fix_file(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Add await to client.find_closest_match
    content = re.sub(r'(\w+ = )client\.find_closest_match\(', r'\1await client.find_closest_match(', content)
    
    # Make test functions async
    content = re.sub(r'def test_csv_lca_rules\(\):', r'async def test_csv_lca_rules():', content)
    content = re.sub(r'def test_document_examples\(\):', r'async def test_document_examples():', content)
    content = re.sub(r'def test_additional_prompts\(\):', r'async def test_additional_prompts():', content)
    
    # Await them in main
    content = re.sub(r'(?<!def )test_csv_lca_rules\(\)', r'await test_csv_lca_rules()', content)
    content = re.sub(r'(?<!def )test_document_examples\(\)', r'await test_document_examples()', content)
    content = re.sub(r'(?<!def )test_additional_prompts\(\)', r'await test_additional_prompts()', content)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file('test_full_logic.py')
fix_file('test_final_check.py')
print('Fixed tests')
