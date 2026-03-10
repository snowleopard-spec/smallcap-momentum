import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("POLYGON_API_KEY")
url = f"https://api.polygon.io/v3/reference/tickers/AAPL?apiKey={api_key}"

response = requests.get(url)
data = response.json()

print(f"Status: {response.status_code}")
print(f"Company: {data['results']['name']}")
print(f"Market Cap: ${data['results'].get('market_cap', 'N/A'):,.0f}")

