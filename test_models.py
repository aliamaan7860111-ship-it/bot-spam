import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get('ANTHROPIC_API_KEY')
headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01'}
url = 'https://api.anthropic.com/v1/models'
try:
    response = requests.get(url, headers=headers)
    print('Status:', response.status_code)
    print('Response:', response.json())
except Exception as e:
    print('Exception:', e)
