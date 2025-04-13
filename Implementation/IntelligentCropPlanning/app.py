# app.py
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
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
import uuid
from botocore.exceptions import ClientError
import json 
from urllib.parse import unquote
#from your_aws_module import users_table, ClientError



app = Flask(__name__)

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
# DynamoDB connection
dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.getenv('AWS_REGION'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)
users_table = dynamodb.Table('users')
chat_table = dynamodb.Table('chat_history')



#open weather api
api_key='ea284063eb75d6986cbf37b5f104a552' # <====API KEY=======|


load_dotenv()
app.secret_key = os.getenv("SECRET_KEY") # Use a strong random secret key


BASE_URL = "https://api.open-meteo.com/v1/"


client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)


# Get weather data in JSON format
def get_weather_data(api_key, location):
    base_url = 'http://api.openweathermap.org/data/2.5/weather?'
    complete_url = base_url + 'q=' + location + '&appid=' + api_key + '&units=metric'
    response = requests.get(complete_url)
    return response.json()


# Method to get weather data from the 'OpenWeather' API's JSON response 
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




# Method to get longitude and latitue from the 'OpenWeather' API's JSON response  
def get_lon_and_lat(data):
    if data['cod'] == 200:
        coord = data['coord']
        lon = coord['lon']
        lat =coord['lat']
        return lon, lat
    else:
        return {'Error': data.get('message', 'Unable to fetch data')}


# Get PH value of the soil from the 'openepi' API 
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


# Get soil moisture and temprature 
def fetch_soil_data_selenium(lon, lat):
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")  
    chrome_options.add_argument("--disable-gpu") 
    chrome_options.add_argument("--window-size=1920,1080") 
    
    driver = webdriver.Chrome(options=chrome_options)

    url = f"https://soiltemperature.app/results?lat={lat}&lng={lon}"
    driver.get(url)

    #time.sleep(1)

    # Find all rows in the table
    rows = driver.find_elements(By.CLASS_NAME, 'row')

    soil_data = []

    
    for row in rows[1:]:  # Skip the first row (headerr)
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

    driver.quit()

    return soil_data

# Get soil temprature adn moisture data
def fetch_soil_temperature_history(latitude, longitude, days=30):
    """Fetch historical soil temperature data"""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    
    url = f"{BASE_URL}soil"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "soil_temperature_0_to_10cm",
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "auto"
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json()
    return None


# Create dynamo DB table
def create_user_table():
    table = dynamodb.create_table(
        TableName='ChatUsers',
        KeySchema=[
            {'AttributeName': 'username', 'KeyType': 'HASH'},  # Partition key
            {'AttributeName': 'chat_id', 'KeyType': 'RANGE'}   # Sort key
        ],
        AttributeDefinitions=[
            {'AttributeName': 'username', 'AttributeType': 'S'},
            {'AttributeName': 'chat_id', 'AttributeType': 'S'}
        ],
        ProvisionedThroughput={
            'ReadCapacityUnits': 5,
            'WriteCapacityUnits': 5
        }
    )

    print("Creating table...")
    table.wait_until_exists()
    print("Table created successfully!")

#(Already run once to create table)
#create_user_table()


# Get user's IP address
def get_public_ip():
    try:
        response = requests.get('https://api.ipify.org?format=json')
        ip = response.json().get('ip')
        print(ip)
        return get_location_from_ip(ip)
    except:
        return "Could not fetch public IP."


# Get location based on IP
def get_location_from_ip(ip_address):
    try:
        response = requests.get(f'http://ip-api.com/json/{ip_address}').json()
        
        if response['status'] == 'success':
            location =  response.get('city')
            return location
    except Exception as e:
        print(f"Error getting location: {e}")
        return None



def fetch_soil_data(latitude, longitude, days=30):
    """Fetch historical soil temperature and moisture data"""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "soil_temperature_0_to_10cm,soil_moisture_0_to_10cm",
        "start_date": start_date.strftime('%Y-%m-%d'),
        "end_date": end_date.strftime('%Y-%m-%d'),
        "timezone": "auto"
    }
    
    # Use the forecast endpoint which includes soil data
    response = requests.get(f"{BASE_URL}forecast", params=params)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching soil data: {response.status_code} - {response.text}")
        return None


def get_chat_response(messages):
    try:
        chat_completion = client.chat.completions.create(
            model="mistralai/mistral-7b-instruct", 
            messages=messages
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        return f"<p>Error: {str(e)}</p>"

# Get 5 year temprature data from 2019-2024
def get_5years_6month_temps(latitude, longitude):
    base_url = "https://archive-api.open-meteo.com/v1/archive"
    yearly_data = {}

    for year in range(2019, 2024):  # 2019–2023 (5 years)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": f"{year}-04-01",
            "end_date": f"{year}-10-31",  # April–October
            "daily": "temperature_2m_mean",
            "timezone": "auto"
        }
        response = requests.get(base_url, params=params)
        if response.ok:
            yearly_data[year] = response.json()["daily"]
        else:
            yearly_data[year] = None  # Mark failed years

    return yearly_data

# Summarize each month's data in 2019-2024
def summarize_monthly_temps(yearly_data):
    summaries = {}

    for year, data in yearly_data.items():
        if not data:
            continue  # Skip failed years

        daily_temps = data["temperature_2m_mean"]
        daily_dates = data["time"]

        monthly_temps = {
            "April": [], "May": [], "June": [], 
            "July": [], "August": [], "September": [], "October": []
        }

        for date_str, temp in zip(daily_dates, daily_temps):
            date = datetime.strptime(date_str, "%Y-%m-%d")
            month = date.strftime("%B")  # e.g., "April"
            if month in monthly_temps:
                monthly_temps[month].append(temp)

        # Calculate monthly averages
        monthly_avg = {
            month: sum(temps) / len(temps) 
            for month, temps in monthly_temps.items() 
            if temps  # Skip empty months
        }
        summaries[year] = monthly_avg

    return summaries

# Format data to get avergae tempratures like:
# - April: 2019=12.5°C, 2020=13.1°C, 2021=11.8°C, 2022=14.2°C, 2023=15.0°C

def format_prompt_summary(temp_summaries):
    prompt_lines = ["Historical April–October temperatures (2019–2023):"]

    for month in ["April", "May", "June", "July", "August", "September", "October"]:
        month_line = f"- {month}: "
        year_avgs = []
        for year, monthly_avg in temp_summaries.items():
            if month in monthly_avg:
                year_avgs.append(f"{year}={monthly_avg[month]:.1f}°C")
        prompt_lines.append(month_line + ", ".join(year_avgs))


#======================================================================================
#                                  APP ROUTES                                         #
#======================================================================================


@app.route('/', methods=['GET', 'POST'])
def index():

    #Weather card data
    weather_data = None
    ph_value = None
    location = None

    location = get_public_ip()
    
    if request.method == 'POST':  
        location = request.form.get('location')
        print("location form yupup is ", location)
    

    print("location form is ", location)

    weather_json= get_weather_data(api_key, location)
    if weather_json.get('cod') == 200:
        weather_data = parse_weather_data(weather_json)
    else:
        weather_data = {'Error': weather_data.get('message', 'Unable to fetch data')}
    
    ph_value = get_ph_value(weather_json)

    
    lng = weather_data.get('lon', 0)
    lat = weather_data.get('lat',0)
    days = request.args.get('days', '30')
    
    try:
        lat = float(lat)
        lng = float(lng)
        days = int(days)
    except:
        lat, lng, days = 49.2609, -123.1139, 30
    
    # Fetch soil data
    soil_data = fetch_soil_data(lat, lng, days)
    
    # Prepare data for charts
    chart_data = {
        'temperature': {
            'labels': [],
            'hourly': [],
            'daily_avg': []
        },
        'moisture': {
            'labels': [],
            'hourly': [],
            'daily_avg': []
        }
    }
    
    if soil_data and 'hourly' in soil_data:
        # Process hourly data to create daily averages
        hourly_data = soil_data['hourly']
        daily_temp = {}
        daily_moisture = {}
        
        # Check if required data exists in response
        if ('time' in hourly_data and 
            'soil_temperature_0_to_10cm' in hourly_data and 
            'soil_moisture_0_to_10cm' in hourly_data):
            
            for i in range(len(hourly_data['time'])):
                date = hourly_data['time'][i][:10]  # Extract YYYY-MM-DD
                
                # Temperature data
                if date not in daily_temp:
                    daily_temp[date] = []
                if i < len(hourly_data['soil_temperature_0_to_10cm']):
                    daily_temp[date].append(hourly_data['soil_temperature_0_to_10cm'][i])
                
                # Moisture data
                if date not in daily_moisture:
                    daily_moisture[date] = []
                if i < len(hourly_data['soil_moisture_0_to_10cm']):
                    daily_moisture[date].append(hourly_data['soil_moisture_0_to_10cm'][i])
            
            # Calculate daily averages
            for date, temps in sorted(daily_temp.items()):
                if len(temps) > 0:  # Fixed: using len() instead of .length
                    avg_temp = sum(temps) / len(temps)
                    chart_data['temperature']['labels'].append(date)
                    chart_data['temperature']['daily_avg'].append(round(avg_temp, 1))
            
            for date, moistures in sorted(daily_moisture.items()):
                if len(moistures) > 0:  # Fixed: using len() instead of .length
                    avg_moisture = sum(moistures) / len(moistures)
                    chart_data['moisture']['labels'].append(date)
                    chart_data['moisture']['daily_avg'].append(round(avg_moisture, 3))
            
            # Include all hourly points
            chart_data['temperature']['hourly'] = list(zip(
                hourly_data['time'],
                hourly_data['soil_temperature_0_to_10cm']
            )) if 'soil_temperature_0_to_10cm' in hourly_data else []
            
            chart_data['moisture']['hourly'] = list(zip(
                hourly_data['time'],
                hourly_data['soil_moisture_0_to_10cm']
            )) if 'soil_moisture_0_to_10cm' in hourly_data else []
    
        
    data = get_5years_6month_temps(lat,lng) 
    temp_summaries = summarize_monthly_temps(data)
    summary_for_prompt = format_prompt_summary(temp_summaries)



    crop_prompt = f"""
    Given historical temperature trends in {location} (April–October, 2019–2023):
    {summary_for_prompt}
    - Soil pH: {ph_value}
    - Average soil temperature: {chart_data['temperature']['daily_avg'][-1] if chart_data['temperature']['daily_avg'] else 'unknown'}°C
    - Average soil moisture: {chart_data['moisture']['daily_avg'][-1] if chart_data['moisture']['daily_avg'] else 'unknown'}m³/m³

    Provide exactly 3 crop recommendations in JSON format with this structure:
    {{
      "recommendations": [
        {{
          "name": "Crop Name",
          "reason": "Brief reason",
          "planting_window": "Month range",
          "soil_temp_range": "X-Y°C",
          "ph_range": "A-B",
          "water_needs": "Low/Medium/High"
        }}
      ]
    }}
    """

    try:
        response = client.chat.completions.create(
            model="mistralai/mistral-7b-instruct",
            messages=[
                {"role": "system", "content": "You are an agricultural expert. Provide exactly 3 crop recommendations in valid JSON format."},
                {"role": "user", "content": crop_prompt}
            ],
            response_format={"type": "json_object"}
        )
        if response.choices[0].message.content:
            crop_recommendations = json.loads(response.choices[0].message.content)
        else:
            crop_recommendations = {"recommendations": []}
        print(response)
    except Exception as e:
        crop_recommendations = {"recommendations": []}

    
    return render_template(
        'index.html',
        lat=lat,
        lng=lng,
        days=days,
        chart_data=json.dumps(chart_data),
        last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        weather_data=weather_data,
        ph_value=ph_value,
        crop_recommendations=crop_recommendations,
        location=location
    )



@app.route('/crop-details')
def crop_details():
    try:
        crop_name = unquote(request.args.get('crop', ''))
        location = unquote(request.args.get('location', ''))
        
        # Add validation for special characters
        if not re.match(r'^[\w\s\-()]+$', crop_name):
            return jsonify({"error": "Invalid crop name format"}), 400

        # Update the prompt to handle botanical names
        detailed_prompt = f"""
        Provide weekly growth parameters for {crop_name} in {location}.
        Use this EXACT JSON structure:
        {{
            "weekly_requirements": [
                {{
                    "week": 1,
                    "water_mm": 20,
                    "temp_min_c": 15,
                    "temp_max_c": 25,
                    "sunshine_hours": 6,
                    "fertilizer_npk": "N:10, P:20, K:32 ",
                    "summary": "Sow seeds 1in deep, water lightly daily, maintain 15-25°C soil temp "
            ]
                }},
                {{
                    "week": 2,
                    "water_mm": 25,
                    "temp_min_c": 16,
                    "temp_max_c": 26,
                    "sunshine_hours": 6,
                    "fertilizer_npk": "skip fertilizer this week",
                    "summary": "Water as needed, maintain 14-23°C soil temprature, maintain 15-25°C soil temp"
            ]
                }}
                // Continue for all weeks until harvest
            ]
        }}
        Important:
        1. Include ALL weeks until harvest
        2. Show PROGRESSIVE changes in values
        3. Return ONLY the JSON with no additional text
        4. Summary must be:1-2 sentences, Under 50 words, Action-oriented and must Include key numbers
            ]
        
        Output ONLY the JSON with no additional text or formatting.
        """

        #Generate response 
        response = client.chat.completions.create(
            model="mistralai/mistral-7b-instruct",
            messages=[
                {
                    "role": "system", 
                    "content": "You are an agricultural data system. Respond ONLY with valid JSON. No explanations."
                },
                {"role": "user", "content": detailed_prompt}
            ],
            response_format={"type": "json_object"}
        )
        print(response)

        # JSON extraction
        raw_response = response.choices[0].message.content
        json_str = re.sub(r'[\x00-\x1F]+', '', raw_response)  # Remove control characters
        json_str = re.search(r'\{.*\}', json_str, re.DOTALL).group()
        data = json.loads(json_str)
        
        return jsonify(data)

    except Exception as e:
        return jsonify({
            "error": "Failed to process crop data",
            "details": str(e),
            "received_data": raw_response
        }), 500




@app.route('/care-plan', methods=['GET', 'POST'])
def care_plan():
    return render_template('care_plan.html', today=datetime.now().strftime('%Y-%m-%d'))



@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():

    # On reload (GET), clear chat history (start a new session)
    if request.method == 'GET':
        session['chat_history'] = []
    
    # If there is no chat history, start new prompt
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
        email = request.form.get('email', '').strip().lower()  # Normalize email case
        password = request.form.get('password', '').strip()

        if not email or not password:
            flash('Please enter both email and password.', 'warning')
            return render_template('login.html')

        try:
            
            print(f"Attempting login for email: {email}")
            
            response = users_table.get_item(Key={'email': email})
            
            
            print("DynamoDB response:", response)
            
            if 'Item' not in response:
                flash('Invalid email or password.', 'danger')
                return render_template('login.html')
            
            user = response['Item']
            
            # Debug: Print the stored user data
            print("User data from DB:", user)
            
            # Compare passwords (assuming you're storing hashed passwords)
            if check_password_hash(user.get('password'), password):
                session['email'] = email
                session['user_id'] = user.get('user_id')  # If you have a user_id
                flash('Login successful!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Invalid email or password.', 'danger')

        except ClientError as e:
            flash('Server error during login. Please try again.', 'danger')
            print(f"DynamoDB Error: {e.response['Error']['Message']}")
        except Exception as e:
            flash('An unexpected error occurred.', 'danger')
            print(f"Unexpected Error: {str(e)}")

    return render_template('login.html')



@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))


def save_chat_to_dynamodb(user_email, user_message, bot_response):
    chat_table.put_item(
        Item={
            'chat_id': str(uuid.uuid4()),
            'user_email': user_email,
            'timestamp': datetime.utcnow().isoformat(),
            'user_message': user_message,
            'bot_response': bot_response
        }
    )

def get_user_chats(user_email):
    response = chat_table.query(
        IndexName='user_email-timestamp-index',  # Assumes GSI on user_email + timestamp
        KeyConditionExpression=boto3.dynamodb.conditions.Key('user_email').eq(user_email),
        ScanIndexForward=False  
    )
    return response.get('Items', [])


@app.route('/chat', methods=['POST'])
def chat():
    user_input = request.form['message']
    user_email = session.get('email')  # Assuming email stored in session

    # Call your AI model or OpenAI here
    bot_response = call_model(user_input)

    # Save to DynamoDB
    save_chat_to_dynamodb(user_email, user_input, bot_response)

    return jsonify({'response': bot_response})

@app.route('/chat-history')
def chat_history():
    user_email = session.get('email')
    history = get_user_chats(user_email)
    return render_template('chat.html', history=history)


if __name__ == '__main__':
    app.run(debug=True)
