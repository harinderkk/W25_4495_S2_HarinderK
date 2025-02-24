# app.py
from flask import Flask, render_template, request
import pandas as pd
import requests
from httpx import Client
import re 
from selenium import webdriver
from selenium.webdriver.common.by import By
import time
from datetime import datetime, timedelta
import calendar

api_key='ea284063eb75d6986cbf37b5f104a552' # <====API KEY=======|

def get_weather_data(api_key, location):
    base_url = 'http://api.openweathermap.org/data/2.5/weather?'
    complete_url = base_url + 'q=' + location + '&appid=' + api_key + '&units=metric'
    response = requests.get(complete_url)
    return response.json()




# Method to get weather data from the 'OpenWeather' API's JSON response ========================
def parse_weather_data(data):
    if data['cod'] == 200:  # Check HTTP 'OK' Status Code
        main = data['main']
        coord = data['coord']
        lon = coord['lon']
        lat =coord['lat']

        if 'rain' in data: # If there is no rain today
            rain_json = data['rain']
            rain = rain_json['1h']
        else:
            rain = 0
        
        weather_desc = data['weather'][0]['description']
        weather_data = {
            'lon': lon,
            'lat': lat,
            'temperature': main['temp'],
            'humidity': main['humidity'],
            'description': weather_desc,
            'rain':rain
        }
        return weather_data
    else:
        return {'Error': data.get('message', 'Unable to fetch data')}




# Method to get longitude and latitue from the 'OpenWeather' API's JSON response ===============   
def get_lon_and_lat(data):
    if data['cod'] == 200:
        coord = data['coord']
        lon = coord['lon']
        lat =coord['lat']
        return lon, lat
    else:
        return {'Error': data.get('message', 'Unable to fetch data')}
    
    



# Get PH value of the soil from the 'openepi' API ===============================================
def get_ph_value(weather_data):
    with Client() as client:
        # Get the mean and the 0.05 quantile of the soil properties at the queried location and depths
        lon, lat = get_lon_and_lat(weather_data) #method call 
        response_multi = client.get(
        url="https://api.openepi.io/soil/property",
        params={
                "lat": lat,
                "lon": lon,
                "depths": ["0-5cm", "100-200cm"],
                "properties": ["bdod", "phh2o"],
                "values": ["mean", "Q0.05"],
            },
        )

        json_multi = response_multi.json()
    
        #    Get the soil information for the phh2o property
        phh2o = json_multi["properties"]["layers"][1]
    
        # Get the ph 0.05 quantile value at depth 100-200cm
        phh2o_value = phh2o["depths"][1]["values"]["Q0.05"]/10

        
        return phh2o_value


# Get soil moisture and temprature ====================================================
def fetch_soil_data_selenium(lon, lat):
    
    driver = webdriver.Chrome()  

    # Open the website
    url = f"https://soiltemperature.app/results?lat={lat}&lng={lon}"
    driver.get(url)

    #time.sleep(1)

    # Find all rows in the table
    rows = driver.find_elements(By.CLASS_NAME, 'row')

    # Initialize a list to store the data
    soil_data = []

    # Iterate through each row
    for row in rows[1:]:  # Skip the first row (header)
        # Find all columns in the row
        cols = row.find_elements(By.CLASS_NAME, 'col')

        # Check if the row has at least 3 columns which are depth, temperature, moisture
        if len(cols) >= 3:
            depth = cols[0].text
            temperature = cols[1].text
            moisture = cols[2].text

            # Append the data to the list
            soil_data.append({
                'depth': depth,
                'temperature': temperature,
                'moisture': moisture
            })

    # Close the browser
    driver.quit()

    return soil_data



#================App Routes=========================================================

app = Flask(__name__)


@app.route('/weather', methods=['GET', 'POST'])
def weather():
    weather_data = None
    ph_value = None
    if request.method == 'POST':  
        location = request.form['location']

        weather_json= get_weather_data(api_key, location)
        if weather_json.get('cod') == 200:
            weather_data = parse_weather_data(weather_json)
        else:
            weather_data = {'Error': weather_data.get('message', 'Unable to fetch data')}
        

        ph_value = get_ph_value(weather_json)
    return render_template('weather.html', weather_data=weather_data, ph_value=ph_value)



@app.route('/')
def home():
    return render_template('index.html')




@app.route('/crop-journey', methods=['GET', 'POST'])
def crop_selection():
    recommendations =""
    if request.method == 'POST':
        selected_crop = request.form['crop']
        location = request.form['location']
        
        weather_json= get_weather_data(api_key, location)
        
        if weather_json.get('cod') == 200:
            weather_data = parse_weather_data(weather_json)
        else:
            weather_data = {'Error': weather_data.get('message', 'Unable to fetch data')}
        
        air_temperature = weather_data.get('temperature')
        humidity = weather_data.get('humidity')
        rain = weather_data.get('rain')
        lon = weather_data.get('lon')
        lat = weather_data.get('lat')


        print("data is " , lat, lon, air_temperature , humidity, rain)

        ph_value = get_ph_value(weather_json)

        soil_data = fetch_soil_data_selenium(lon,lat)
        
        # Filter data for depth 6 cm and soil-specific data
        depth_to_find = "6 cm"
        filtered_data = [
            data for data in soil_data 
            if data['depth'] == depth_to_find 
            and '°C' in data['temperature']  # Ensure it's temperature in °C
            and 'm³/m³' in data['moisture']  # Ensure it's moisture in m³/m³
        ]

        soil_temperature = None 
        if filtered_data:
            for data in filtered_data:
                #soil_temperature = float(data['temperature'])
                soil_temperature = float(re.findall(r"-?\d+\.?\d*", data['temperature'])[0])

                #soil_moisture =  float(data['moisture'])
                soil_moisture = float(re.findall(r"-?\d+\.?\d*", data['moisture'])[0])

        else:
            print(f"No soil data found for depth: {depth_to_find}")
        

        #==== using crop_conditoins csv to generate recommendations====


        # Convert user input and dataset crop names to lowercase
        selected_crop = selected_crop.lower()
        df_crop_requirements = pd.read_csv('growth_conditions.csv')
        df_crop_requirements['Crop'] = df_crop_requirements['Crop'].str.lower()

        # Filter the DataFrame for the selected crop
        crop_data_filtered = df_crop_requirements[df_crop_requirements['Crop'] == selected_crop]

        # Check if the crop exists in the dataset
        if crop_data_filtered.empty:
            print(f"Error: '{selected_crop}' is not in the dataset. Please select a valid crop.")
        else:
            # Access the first row of the filtered DataFrame
            crop_data = crop_data_filtered.iloc[0]



        # Provide crop-specific information
        growth_season = crop_data['Growth Season (Months)']
        print(f"\nYou need {growth_season} months to sow, grow, and harvest {selected_crop}.")

        min_temp = float(crop_data['Min Temp (°C)'])
        max_temp = float(crop_data['Max Temp (°C)'])
        min_soil_temp = float(crop_data['Min Soil Temp (°C)'])
        max_soil_temp = float(crop_data['Max Soil Temp (°C)'])
        ideal_ph_min = 6.0  # Example ideal pH range for most crops
        ideal_ph_max = 7.0
        water_needs = crop_data[['Water Needs (mm) - Month 1', 'Water Needs (mm) - Month 2', 'Water Needs (mm) - Month 3', 
                                 'Water Needs (mm) - Month 4', 'Water Needs (mm) - Month 5', 'Water Needs (mm) - Month 6']].values
        
        # Compare temperature
        if air_temperature < min_temp:
            temp_recommendation = f"The current temperature ({air_temperature}°C) is too low for {selected_crop}. It requires a minimum temperature of {min_temp}°C. Consider growing in a greenhouse or during warmer months."
        elif air_temperature > max_temp:
            temp_recommendation = f"The current temperature ({air_temperature}°C) is too high for {selected_crop}. It requires a maximum temperature of {max_temp}°C. Consider providing shade or growing during cooler months."
        else:
            temp_recommendation = f"The current temperature ({air_temperature}°C) is ideal for {selected_crop}."

        # Compare soil temperature
        if soil_temperature < min_soil_temp:
            soil_temp_recommendation = f"The current soil temperature ({soil_temperature}°C) is too low for {selected_crop}. It requires a minimum soil temperature of {min_soil_temp}°C. Consider using mulch or a soil heater."
        elif soil_temperature > max_soil_temp:
            soil_temp_recommendation = f"The current soil temperature ({soil_temperature}°C) is too high for {selected_crop}. It requires a maximum soil temperature of {max_soil_temp}°C. Consider cooling the soil with shade or irrigation."
        else:
            soil_temp_recommendation = f"The current soil temperature ({soil_temperature}°C) is ideal for {selected_crop}."

        # Compare rainfall
        if rain < water_needs[0]:  # Compare with Month 1 water needs
            rainfall_recommendation = f"The current rainfall ({rain} mm) is insufficient for {selected_crop}. It requires {water_needs[0]} mm in the first month. Consider irrigating regularly."
        else:
            rainfall_recommendation = f"The current rainfall is sufficient for {selected_crop}."

        # Compare soil pH
        if ph_value < ideal_ph_min:
            ph_recommendation = f"The soil pH ({ph_value}) is too acidic for {selected_crop}. Consider adding lime to raise the pH."
        elif ph_value> ideal_ph_max:
            ph_recommendation = f"The soil pH ({ph_value}) is too alkaline for {selected_crop}. Consider adding sulfur or organic matter to lower the pH."
        else:
            ph_recommendation = f"The soil pH is ideal for {selected_crop}."

        # Provide recommendations
        current_recommendations = [selected_crop, temp_recommendation, soil_temp_recommendation, rainfall_recommendation,
        ph_recommendation]


    return render_template('crop-journey.html', recommendations=recommendations)






@app.route('/care-plan', methods=['GET', 'POST'])
def care_plan():
    if request.method == 'POST':
        # Get user inputs
        selected_crop = request.form['crop'].strip().lower()  # Clean input
        location = request.form['location']
        planting_date = datetime.strptime(request.form['planting_date'], '%Y-%m-%d')
        current_date = datetime.now()

        # Fetch weather data
        weather_json = get_weather_data(api_key, location)
        if weather_json.get('cod') != 200:
            return render_template('care_plan.html', error="Location not found")
        weather_data = parse_weather_data(weather_json)
        lon = weather_data.get('lon', 0)


        # Load crop data
        df_crop = pd.read_csv('csv-files/growth-conditions.csv')
        df_crop['Crop'] = df_crop['Crop'].str.strip().str.lower()  # Clean CSV data

        # Validate crop exists
        crop_filter = df_crop['Crop'] == selected_crop
        if not crop_filter.any():
            return render_template('care_plan.html', 
                                error=f"Crop '{selected_crop}' not found in database. Please select a valid crop.")
        
        crop_data = df_crop[crop_filter].iloc[0]  # Safe after validation

        # Get ideal planting months
        print(lon, 'and')
        if lon >= 0:
            hemisphere = 'Northern' 
        else:
            hemisphere = 'Southern'
        if hemisphere == 'Northern':
            start_month = crop_data['NH_Planting_Start']
            end_month = crop_data['NH_Planting_End']
        elif hemisphere == 'Southern':
            start_month = crop_data['SH_Planting_Start']
            end_month = crop_data['SH_Planting_End']

        # Calculate next valid planting date
        current_year = datetime.now().year
        current_month = datetime.now().month
        if start_month <= end_month:
            valid_months = list(range(start_month, end_month + 1))
        else:
            valid_months = list(range(start_month, 13)) + list(range(1, end_month + 1))

        candidates = [m for m in valid_months if m > current_month]
        if candidates:
            next_valid_month = min(candidates)
            next_year = current_year
        else:
            next_valid_month = valid_months[0]
            next_year = current_year + 1

        suggested_date = datetime(next_year, next_valid_month, 1)
        message = None

        # Validate planting date
        is_ideal = (
            (start_month <= planting_date.month <= end_month) 
            if start_month <= end_month 
            else (planting_date.month >= start_month or planting_date.month <= end_month)
        )

        # Check if the user's selected date is within or outside the season
        if planting_date > current_date and not is_ideal:
            suggested_date = datetime(next_year + 1, next_valid_month, 1)
            message = (
                f"⚠️ The best time to plant this crop is between {calendar.month_name[start_month]}-{calendar.month_name[end_month]}. "
                f"Consider planting next year on: {suggested_date.strftime('%d %B, %Y')}"
            )
        elif planting_date <= current_date and not is_ideal:
            message = (
                f"⚠️ The best time to plant this crop is between {calendar.month_name[start_month]}-{calendar.month_name[end_month]}. "
                f"The season starts on: {suggested_date.strftime('%d %B, %Y')}"
            )
        else:
            message = (
                f"You're currently on the optimal timeline for planting! The best time to grow is between {calendar.month_name[start_month]}-{calendar.month_name[end_month]}. "
                f"You can start planting this crop."
            )

        # Calculate timeline
        elapsed_days = (current_date - planting_date).days
        elapsed_weeks = elapsed_days // 7
        current_week = max(elapsed_weeks + 1, 1)  # Ensure minimum week 1
        growth_months = crop_data['Growth Season (Months)']
        total_weeks = int(growth_months * 4)
        total_days = int(growth_months * 30)
        harvest_date = planting_date + timedelta(days=total_days)

        # Generate recommendations
        recommendations = {
            'crop': selected_crop.title(),
            'current_week': min(current_week, total_weeks),
            'total_weeks': total_weeks,
            'weekly_plan': [],
            'actions': []
        }

        # Weekly plan logic
        for week in range(recommendations['current_week'], total_weeks + 1):
            month = ((week - 1) // 4) + 1
            month = max(min(month, 12), 1)  # Clamp between 1-12
            
            water_key = f'Water Needs (mm) - Month {month}'
            if water_key not in crop_data:
                continue

            weekly_plan_entry = {
                'week': week,
                'water': crop_data[water_key],
                'air_temp': f"{crop_data['Min Temp (°C)']}-{crop_data['Max Temp (°C)']}°C",
                'soil_temp': f"{crop_data['Min Soil Temp (°C)']}-{crop_data['Max Soil Temp (°C)']}°C"
            }
            recommendations['weekly_plan'].append(weekly_plan_entry)

        # Get Soil data
        try:
            ph_value = get_ph_value(weather_json)
            soil_data = fetch_soil_data_selenium(weather_data['lon'], weather_data['lat'])
            # ... (soil data parsing logic)
        except Exception as e:
            ph_value = 5.8
            soil_temperature = soil_moisture = None

        return render_template(
            'care_plan.html',
            recommendations=recommendations,
            harvest_date=harvest_date.strftime('%d %B, %Y'),
            weeks_remaining=total_weeks - recommendations['current_week'],
            future_planting_message=message,
            ph_value=ph_value,
            location=location,
            today=datetime.now().strftime('%Y-%m-%d')
        )

    return render_template('care_plan.html', today=datetime.now().strftime('%Y-%m-%d'))


if __name__ == '__main__':
    app.run(debug=True)
