# DR Horton Data Scraper

This project scrapes community and home information from DR Horton's website.

## Setup

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Make sure you have Chrome browser installed

3. Create the data directory structure:
```bash
mkdir -p data/drhorton
```

## Running the Scripts

1. First run the page scraper:
```bash
python get_drhorton_page.py
```

This will create the necessary JSON output file in the current directory. 