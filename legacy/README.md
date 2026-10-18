# US Immigrant Visa Statistics Dashboard

This project creates an interactive dashboard to visualize monthly immigrant visa issuance statistics from the US Department of State.

## Overview

The dashboard scrapes data from the US Department of State website and visualizes various immigrant visa categories (IR1, F1, F2B, F3, F4) over time. It provides interactive filtering, comparison charts, and summary statistics to help understand immigration trends.

## Features

- Automated data scraping from [travel.state.gov](https://travel.state.gov/content/travel/en/legal/visa-law0/visa-statistics/immigrant-visa-statistics/monthly-immigrant-visa-issuances.html)
- Interactive visualizations including:
  - Time series plots of visa issuances over time
  - Comparative bar charts between visa categories
  - Summary statistics tables and pie charts
- Filtering options by:
  - Visa class
  - Date range
- Data refreshing capabilities

## Visa Categories Explained

- **IR1**: Immediate Relatives (Spouse of U.S. Citizen)
- **F1**: Family-Sponsored Preferences (Unmarried Sons/Daughters of U.S. Citizens)
- **F2B**: Family-Sponsored Preferences (Unmarried Sons/Daughters of Permanent Residents)
- **F3**: Family-Sponsored Preferences (Married Sons/Daughters of U.S. Citizens)
- **F4**: Family-Sponsored Preferences (Siblings of U.S. Citizens)

## Installation

1. Clone this repository
2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

1. Run the Streamlit dashboard:
   ```
   streamlit run dashboard.py
   ```
2. The app will automatically scrape data if it's not already downloaded
3. Use the sidebar filters to customize your view
4. Navigate through tabs to see different visualizations

## Project Structure

- `scraper.py`: Python script to download and process visa data
- `dashboard.py`: Streamlit application for visualizing the data
- `requirements.txt`: List of dependencies
- `data/`: Directory for storing raw and processed data
  - `data/raw/`: Raw Excel files downloaded from the source
  - `data/visa_data.xlsx`: Processed data file

## Requirements

- Python 3.7+
- Streamlit
- Pandas
- Matplotlib
- BeautifulSoup4
- Requests
- Openpyxl

## Data Source

All data is sourced from the [US Department of State website](https://travel.state.gov/content/travel/en/legal/visa-law0/visa-statistics/immigrant-visa-statistics/monthly-immigrant-visa-issuances.html).
