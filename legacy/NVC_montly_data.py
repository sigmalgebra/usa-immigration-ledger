import streamlit as st
import pandas as pd 
import matplotlib.pyplot as plt 
import matplotlib.style as style
import openpyxl


def main():
    st.set_page_config(page_title="NVC Montly Statistics", page_icon=":robot:")
    st.title("NVC Montly Statistics")
    st.sidebar.title("NVC Montly Statistics")
    st.markdown("Check out the new NVC statistics")
    
    @st.cache_data(persist=True)
    def load_data():
        data=pd.read_excel("MontlyImmigrationData.xlsx")
        return data
    
    data=load_data()
    visaClass=st.sidebar.multiselect("Visa Class",("IR1","F1","F2B","F3","F4"))
    #st.write(data.head())
    

    def plot_stat(visaClass):
        style.use('fivethirtyeight')
        fig,ax=plt.subplots()
       # colors = [[0,0,0], [230/255,159/255,0], [86/255,180/255,233/255], [0,158/255,115/255],[240/255,228/255,66/255], [0,114/255,178/255], [213/255,94/255,0], [204/255,121/255,167/255]]
        ax.plot(data['MonthYear'],data[visaClass],label=visaClass)
        ax.tick_params(axis='both',which='major',labelsize=10)
        plt.xticks(rotation=65,ha='right')
        #plt.yticks([0, 2000, 4000, 6000, 8000])
        ax.set_yticklabels(labels=[2000,0,'2000 ','4000 ','6000 ','8000 '])
        ax.axhline(y=0,color = 'black', linewidth = 2, alpha = .7)
        ax.xaxis.label.set_visible(False)
        ax.legend(loc='best')



        #ax.set_xlim(data['MonthYear'])
        #ax.set_xlabel('Month and Year')
        #ax.set_ylabel('F1 Visa Issuances')
        # The signature bar
        ax.text(x = 24, y = -4400,s = 'Source: State department', fontsize = 6,
                        color = '#f0f0f0', backgroundcolor = 'grey')
        ax.text(x=0.1,y=9600,s="Visa Issued",fontsize = 22, weight = 'bold', alpha = .75)
        ax.text(x=0.1,y=9100,s="Visa Issued by class in every country",fontsize = 12, alpha = .85)
        
        


        # plt.xticks(rotation=45)
        st.pyplot(fig)

    
    col1,col2=st.columns(2)
    with col1:
        st.markdown("Have a look at the [National Visa Center](https://travel.state.gov/content/travel/en/us-visas/immigrate/national-visa-center.html) statistics\
                \n\nThis tool is powered by and made by \
                [@addp](https://twitter.com/31GregKamradt). \n\n View Source Code on [Github](https://github.com/gkamradt/globalize-text-streamlit/blob/main/main.py)")

    with col2:
        st.markdown('')
        #st.image(image='Researcher.png', width=300, caption='Mid Journey: A researcher who is really good at their job and utilizes twitter to do research about the person they are interviewing. playful, pastels. --ar 4:7')
        # End Top Information
    
    st.markdown("hello there this is a text message to test if the two coulmns are still on or if the columns are disapperade \n\n i think ther are disappered ")

    plot_stat(visaClass)









if __name__=='__main__':
    main()

    
# st.write(data.head())
# fig,ax=plt.subplots()
# ax.scatter(data['MonthYear'],data['F1'])
# ax.set_xlabel('Month and Year')
# ax.set_ylabel('F1 Visa Issuances')


# plt.xticks(rotation=45)

# # Display the plot in Streamlit
# st.pyplot(fig)