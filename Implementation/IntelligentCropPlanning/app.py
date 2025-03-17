# app.py
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import pandas as pd
import requests
from httpx import Client
import re 
from selenium import webdriver
from selenium.webdriver.common.by import By
import time
from datetime import datetime, timedelta
import calendar
import os
from dotenv import load_dotenv
import openai
from openai import OpenAI
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
import boto3


app = Flask(__name__)

#open weather api
api_key='ea284063eb75d6986cbf37b5f104a552' # <====API KEY=======|

load_dotenv()
app.secret_key = os.getenv("SECRET_KEY") # Use a strong random secret key


client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

# DynamoDB connection
dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.getenv('AWS_REGION'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)

users_table = dynamodb.Table('users')

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

        if (
            "properties" in json_multi
            and "layers" in json_multi["properties"]
            and len(json_multi["properties"]["layers"]) > 1
        ):
            phh2o = json_multi["properties"]["layers"][1]
            
            # Check if the phh2o layer contains the expected data
            if (
                "depths" in phh2o
                and len(phh2o["depths"]) > 1
                and "values" in phh2o["depths"][1]
                and "Q0.05" in phh2o["depths"][1]["values"]
            ):
                phh2o_value = phh2o["depths"][1]["values"]["Q0.05"]
                
                # Check if the value is not None before dividing
                if phh2o_value is not None:
                    return phh2o_value / 10
        
        # If any of the checks fail, return the default value of 5.8
        return 5.8


# Get soil moisture and temprature ====================================================
def fetch_soil_data_selenium(lon, lat):
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode
    chrome_options.add_argument("--disable-gpu")  # Disable GPU acceleration (often needed for headless)
    chrome_options.add_argument("--window-size=1920,1080") 
    
    driver = webdriver.Chrome(options=chrome_options)


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


'''
@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():
    if request.method == 'GET':
        session['chat_history'] = []

    if 'chat_history' not in session:
        session['chat_history'] = [
            {"role": "system", "content": "You are a helpful farming assistant that answers questions about crops, irrigation, and agriculture."}
        ]

    if request.method == 'POST':
        user_input = request.form['user_input']
        session['chat_history'].append({"role": "user", "content": user_input})
        
        bot_response = get_chat_response(session['chat_history'])
        session['chat_history'].append({"role": "assistant", "content": bot_response})
        session.modified = True  # Mark session as changed

        return render_template('chatbot.html', response=bot_response, chat_history=session['chat_history'])

    return render_template('chatbot.html', response=None, chat_history=session.get('chat_history', []))


def get_chat_response(messages):
    try:
        chat_completion = client.chat.completions.create(
            model="mistralai/mistral-7b-instruct:free",
            messages=messages
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"

'''





@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():
    # On GET, clear chat history (i.e., start a new session)
    if request.method == 'GET':
        session['chat_history'] = []
    
    # If there is no chat history, initialize with a system prompt
    if 'chat_history' not in session or not session['chat_history']:
        session['chat_history'] = [
            {"role": "system", "content": "You are a helpful farming assistant that answers questions about crops, irrigation, and agriculture."}
        ]

    if request.method == 'POST':
        user_input = request.form['user_input']
        session['chat_history'].append({"role": "user", "content": user_input})
        
        # Get bot response using the full conversation history
        bot_response = get_chat_response(session['chat_history'])
        session['chat_history'].append({"role": "assistant", "content": bot_response})
        session.modified = True  # Mark session as changed
        
        # If the user is logged in, save chat history to DynamoDB
        if 'user' in session:
            email = session['user']
            users_table.update_item(
                Key={'email': email},
                UpdateExpression="SET chat_history = :history",
                ExpressionAttributeValues={":history": session['chat_history']}
            )
        
        return render_template('chatbot.html', response=bot_response, chat_history=session['chat_history'])

    return render_template('chatbot.html', response=None, chat_history=session.get('chat_history', []))


def get_chat_response(messages):
    try:
        chat_completion = client.chat.completions.create(
            model="mistralai/mistral-7b-instruct:free",
            messages=messages
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        return f"<p>Error: {str(e)}</p>"



@app.route('/clear_chat', methods=['POST'])
def clear_chat():
    session.pop('chat_history', None)
    return redirect('/chatbot')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        users_table.put_item(
            Item={
                'email': email,
                'password': password,
                'chat_history': []
            }
        )
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        response = users_table.get_item(Key={'email': email})
        user = response.get('Item')

        if user and check_password_hash(user['password'], password):
            session['user'] = email
            return redirect(url_for('chatbot'))
        else:
            return "Login failed"
    return render_template('login.html')


if __name__ == '__main__':
    app.run(debug=True)
