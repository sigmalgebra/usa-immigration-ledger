import requests
from bs4 import BeautifulSoup
import pandas as pd
import os
import re
from datetime import datetime

# Create directories if they don't exist
if not os.path.exists('data'):
    os.makedirs('data')
if not os.path.exists('data/raw'):
    os.makedirs('data/raw')

def scrape_visa_data():
    """
    Scrapes the monthly immigrant visa issuance data from the US Department of State website.
    Downloads Excel files and saves them to the 'data/raw' directory.
    """
    url = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-statistics/immigrant-visa-statistics/monthly-immigrant-visa-issuances.html"
    
    # Send a request to the webpage
    response = requests.get(url)
    
    if response.status_code == 200:
        print("Successfully accessed the website")
        
        # Parse HTML content
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all links to Excel files
        links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Look for Excel files (FSC or Place of Birth)
            if 'FSC' in href and href.endswith('.xlsx'):
                links.append(href)
        
        print(f"Found {len(links)} Excel files to download")
        
        # Download a subset of the most recent files (to avoid downloading too many)
        # Take the last 24 months (2 years) of data
        for link in links[:24]:
            # Get the file name
            file_name = link.split('/')[-1]
            
            # Extract month and year
            match = re.search(r'(\w+)\s*(\d{4})', file_name)
            if match:
                month, year = match.groups()
                
                # Full URL (handle relative URLs)
                full_url = link if link.startswith('http') else f"https://travel.state.gov{link}"
                
                # Download the file
                print(f"Downloading {file_name}...")
                file_response = requests.get(full_url)
                
                if file_response.status_code == 200:
                    # Save to data/raw directory
                    with open(f"data/raw/{file_name}", 'wb') as file:
                        file.write(file_response.content)
                    print(f"Downloaded {file_name}")
                else:
                    print(f"Failed to download {file_name}")
            else:
                print(f"Could not parse month and year from {file_name}")
                
        return True
    else:
        print(f"Failed to access the website. Status code: {response.status_code}")
        return False

def process_visa_data():
    """
    Processes the downloaded Excel files and combines them into a single DataFrame.
    Extracts key visa categories and structures the data for visualization.
    """
    all_data = []
    
    # Visa classes we're interested in
    visa_classes = ['IR1', 'F1', 'F2B', 'F3', 'F4']
    
    # Go through each file in the raw data directory
    for file_name in os.listdir('data/raw'):
        if file_name.endswith('.xlsx'):
            # Extract month and year from filename
            match = re.search(r'(\w+)\s*(\d{4})', file_name)
            if match:
                month, year = match.groups()
                month_year = f"{month} {year}"
                
                try:
                    # Load the Excel file
                    file_path = os.path.join('data/raw', file_name)
                    data = pd.read_excel(file_path, header=1)
                    
                    # Clean column names
                    data.columns = [col.strip() for col in data.columns]
                    
                    # Ensure "Visa Class" column exists
                    if 'Visa Class' in data.columns:
                        # Filter for the visa classes we want
                        filtered_data = data[data['Visa Class'].isin(visa_classes)]
                        
                        # Group and sum issuances by visa class
                        if 'Issuances' in data.columns:
                            grouped_data = filtered_data.groupby('Visa Class')['Issuances'].sum().reset_index()
                            
                            # Create a row with all visa classes and the month/year
                            row = {'MonthYear': month_year}
                            for visa_class in visa_classes:
                                # Get issuances for this visa class, or 0 if not found
                                issuances = grouped_data.loc[
                                    grouped_data['Visa Class'] == visa_class, 'Issuances'
                                ].values[0] if visa_class in grouped_data['Visa Class'].values else 0
                                row[visa_class] = issuances
                            
                            all_data.append(row)
                            print(f"Processed {file_name}")
                        else:
                            print(f"No 'Issuances' column found in {file_name}")
                    else:
                        print(f"No 'Visa Class' column found in {file_name}")
                except Exception as e:
                    print(f"Error processing {file_name}: {e}")
    
    # Create DataFrame from all data
    if all_data:
        df = pd.DataFrame(all_data)
        
        # Convert MonthYear to datetime for sorting
        df['MonthYear'] = pd.to_datetime(df['MonthYear'], format='%B %Y')
        
        # Sort by date
        df = df.sort_values('MonthYear')
        
        # Convert back to string for display
        df['MonthYear'] = df['MonthYear'].dt.strftime('%B %Y')
        
        # Save to processed data file
        df.to_excel('data/visa_data.xlsx', index=False)
        print(f"Data processing complete. Saved to data/visa_data.xlsx")
        return df
    else:
        print("No data was processed.")
        return None

if __name__ == "__main__":
    print("Starting visa data scraping...")
    if scrape_visa_data():
        print("Scraping completed. Processing data...")
        process_visa_data()
        print("All done!")
    else:
        print("Scraping failed.") 