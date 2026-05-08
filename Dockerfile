FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py .

# Volume for persistent seen_listings.json (optional, Railway uses ephemeral FS)
# Data resets on redeploy — acceptable for this use case

CMD ["python", "-u", "scraper.py"]
