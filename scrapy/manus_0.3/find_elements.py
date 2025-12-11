
from bs4 import BeautifulSoup

with open('/home/ubuntu/tribuna_scraper/tribuna_search_results_selenium.html', 'r') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Find all input elements
inputs = soup.find_all('input')
for input_tag in inputs:
    print(f"Found input: {input_tag}")

# Find all button elements
buttons = soup.find_all('button')
for button_tag in buttons:
    print(f"Found button: {button_tag}")

