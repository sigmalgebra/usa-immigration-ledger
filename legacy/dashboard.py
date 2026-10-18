import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
import scraper

# Set page configuration
st.set_page_config(page_title="US Immigrant Visa Statistics", page_icon="🌎", layout="wide")

# Title and description
st.title("US Immigrant Visa Issuance Statistics Dashboard")
st.markdown("""
This dashboard visualizes monthly immigrant visa issuance statistics from the US Department of State.
Data is sourced from [travel.state.gov](https://travel.state.gov/content/travel/en/legal/visa-law0/visa-statistics/immigrant-visa-statistics/monthly-immigrant-visa-issuances.html).
""")

# Create sidebar for data options
st.sidebar.title("Options")

# Function to load or refresh data
@st.cache_data
def load_data():
    if os.path.exists('data/visa_data.xlsx'):
        return pd.read_excel('data/visa_data.xlsx')
    else:
        with st.spinner("No data found. Fetching data from source..."):
            scraper.scrape_visa_data()
            return scraper.process_visa_data()

# Add a refresh data button
if st.sidebar.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# Load the data
data = load_data()

if data is not None:
    # Display a sample of the data
    with st.expander("View Raw Data"):
        st.dataframe(data)
    
    # Prepare data for visualization
    # Convert MonthYear to datetime for proper sorting
    data['MonthYear'] = pd.to_datetime(data['MonthYear'], format='%B %Y')
    data = data.sort_values('MonthYear')
    
    # Visa Classes to visualize
    visa_classes = ['IR1', 'F1', 'F2B', 'F3', 'F4']
    
    # Create a multiselect for visa classes to display
    selected_visas = st.sidebar.multiselect(
        "Select Visa Classes to Display",
        options=visa_classes,
        default=visa_classes
    )
    
    # Display visa class descriptions
    st.sidebar.markdown("""
    ## Visa Class Information
    - **IR1**: Immediate Relatives (Spouse of U.S. Citizen)
    - **F1**: Family-Sponsored Preferences (Unmarried Sons/Daughters of U.S. Citizens)
    - **F2B**: Family-Sponsored Preferences (Unmarried Sons/Daughters of Permanent Residents)
    - **F3**: Family-Sponsored Preferences (Married Sons/Daughters of U.S. Citizens)
    - **F4**: Family-Sponsored Preferences (Siblings of U.S. Citizens)
    """)
    
    # Time range selector
    min_date = data['MonthYear'].min()
    max_date = data['MonthYear'].max()
    
    date_range = st.sidebar.date_input(
        "Select Date Range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date
    )
    
    # Handle single date selection
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = date_range
        end_date = date_range
    
    # Filter data based on selections
    filtered_data = data[
        (data['MonthYear'] >= pd.to_datetime(start_date)) & 
        (data['MonthYear'] <= pd.to_datetime(end_date))
    ]
    
    # Convert back to string format for display
    display_data = filtered_data.copy()
    display_data['MonthYear'] = display_data['MonthYear'].dt.strftime('%B %Y')
    
    # Create tabs for different visualizations
    tab1, tab2, tab3 = st.tabs(["Time Series", "Comparison", "Summary Statistics"])
    
    with tab1:
        st.subheader("Monthly Visa Issuances Over Time")
        
        # Create a time series plot
        fig, ax = plt.subplots(figsize=(12, 6))
        
        for visa in selected_visas:
            ax.plot(filtered_data['MonthYear'], filtered_data[visa], marker='o', linewidth=2, label=visa)
        
        # Formatting
        plt.xticks(rotation=45)
        ax.set_xlabel('Month and Year')
        ax.set_ylabel('Number of Visas Issued')
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        
        # Display the plot
        st.pyplot(fig)
        
        # Add description
        st.markdown("""
        The line chart above shows the number of visas issued each month for the selected visa classes.
        This visualization helps identify trends and seasonality in visa issuances over time.
        """)
    
    with tab2:
        st.subheader("Visa Class Comparison")
        
        # Calculate totals for each visa class
        totals = {visa: filtered_data[visa].sum() for visa in selected_visas}
        
        # Create a bar chart
        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.bar(totals.keys(), totals.values(), color='skyblue')
        
        # Add values on top of bars
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + 5,
                    f'{int(height):,}',
                    ha='center', va='bottom', rotation=0)
        
        ax.set_ylabel('Total Visas Issued')
        ax.set_title('Total Visas Issued by Class (for selected period)')
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        
        # Display the plot
        st.pyplot(fig)
        
        # Monthly averages
        st.subheader("Monthly Averages by Visa Class")
        averages = {visa: filtered_data[visa].mean() for visa in selected_visas}
        
        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.bar(averages.keys(), averages.values(), color='lightgreen')
        
        # Add values on top of bars
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + 5,
                    f'{int(height):,}',
                    ha='center', va='bottom', rotation=0)
        
        ax.set_ylabel('Average Monthly Visas Issued')
        ax.set_title('Average Monthly Visas Issued by Class (for selected period)')
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        
        # Display the plot
        st.pyplot(fig)
    
    with tab3:
        st.subheader("Summary Statistics")
        
        # Create a summary table
        summary = pd.DataFrame({
            'Visa Class': selected_visas,
            'Total Issued': [filtered_data[visa].sum() for visa in selected_visas],
            'Monthly Average': [filtered_data[visa].mean() for visa in selected_visas],
            'Minimum': [filtered_data[visa].min() for visa in selected_visas],
            'Maximum': [filtered_data[visa].max() for visa in selected_visas]
        })
        
        # Format numbers
        summary['Total Issued'] = summary['Total Issued'].astype(int).apply(lambda x: f"{x:,}")
        summary['Monthly Average'] = summary['Monthly Average'].round().astype(int).apply(lambda x: f"{x:,}")
        summary['Minimum'] = summary['Minimum'].astype(int).apply(lambda x: f"{x:,}")
        summary['Maximum'] = summary['Maximum'].astype(int).apply(lambda x: f"{x:,}")
        
        # Display the summary table
        st.table(summary)
        
        # Display overall totals
        total_all_visas = sum(filtered_data[visa].sum() for visa in selected_visas)
        st.metric("Total Visas Issued (All Selected Classes)", f"{total_all_visas:,}")
        
        # Create a pie chart
        fig, ax = plt.subplots(figsize=(10, 10))
        totals = {visa: filtered_data[visa].sum() for visa in selected_visas}
        
        # Create pie chart
        ax.pie(
            totals.values(), 
            labels=totals.keys(), 
            autopct='%1.1f%%',
            startangle=90,
            shadow=False,
            explode=[0.05] * len(selected_visas)
        )
        ax.axis('equal')  # Equal aspect ratio ensures that pie is drawn as a circle
        
        st.pyplot(fig)
        
        st.markdown("""
        The pie chart above shows the relative distribution of visa issuances across the selected classes.
        This helps understand which visa categories make up the largest portion of overall issuances.
        """)

else:
    st.error("No data available. Please check your connection and try again.")

# Footer
st.markdown("---")
st.markdown("Data is sourced from the [US Department of State](https://travel.state.gov/) website.") 