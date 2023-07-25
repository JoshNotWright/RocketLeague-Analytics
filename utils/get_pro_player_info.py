import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
import time
import pickle

# Dictionary to store cached player information
cache = {}

# Path to the cache file
cache_file = 'player_cache.pkl'

# Expiration time for cache data (in seconds)
expiration_time = 24 * 60 * 60 * 7 # 1 week

# Load cached data from file if available
try:
    with open(cache_file, 'rb') as f:
        cache = pickle.load(f)
except FileNotFoundError:
    cache = {}

def extract_team_name(player_url):
    # Make a request to the player's Liquipedia page
    response = requests.get(player_url)

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the HTML content of the page
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find the team information element
        team_element = soup.find('div', class_='infobox-cell-2 infobox-description', string=re.compile(r'Team(s)?:'))

        if team_element:
            # Extract the team name(s) from the next sibling element
            team_names = team_element.find_next_sibling().find_all('a')
            teams = [team.text.strip() for team in team_names]

            # Check team_corrections.txt for corrections
            with open('team_corrections.txt', 'r') as file:
                corrections = file.read().splitlines()

            for team in teams:
                if team in corrections:
                    return team  # Return the corrected team name

            return teams[0]  # Return the first team name if no correction found
        else:
            print('Team information not found on the page')
            return None
    else:
        print('Failed to retrieve player information from Liquipedia')
        return None
    
def extract_age(text):
    if text is None:
        print('No age found in the text')
        return None
    
    # Find the last number in the text using regular expressions
    age_match = re.findall(r'\d+', text)
    if age_match:
        age = age_match[-1]  # Take the last number
        return age
    else:
        print('No age found in the text')
        return None
    
def get_player_age(player_url):
    response = requests.get(player_url)
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')
        
        age_element = soup.find('div', class_='infobox-cell-2 infobox-description', string='Born:')
        
        if age_element:
            age_text = age_element.find_next_sibling().text.strip()
            return age_text
        else:
            print('Player age not found on the page')
            return None
    else:
        return None
    
def extract_team_logo(team_name):
    if team_name is None:
        return None
    
    # Convert the team name to the Liquipedia page URL format
    team_url = f"https://liquipedia.net/rocketleague/{team_name.replace(' ', '_')}"

    # Make a request to the team's Liquipedia page
    response = requests.get(team_url)

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the HTML content of the page
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find the team logo element
        logo_element = soup.find('div', class_='infobox-image lightmode').find('a', class_='image').find('img')

        if logo_element:
            # Extract the link to the team's logo
            logo_link = logo_element['src']
            # Join the relative link with the base Liquipedia URL to get the complete logo URL
            complete_logo_link = urljoin('http://liquipedia.net', logo_link)
            return complete_logo_link
        else:
            print('Team logo not found on the page')
            return None
    else:
        print('Failed to retrieve team information from Liquipedia')
        return None
    
def extract_team_region(team_name):
    if team_name is None:
        return None
    
    # Convert the team name to the Liquipedia page URL format
    team_url = f"https://liquipedia.net/rocketleague/{team_name.replace(' ', '_')}"

    # Make a request to the team's Liquipedia page
    response = requests.get(team_url)

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the HTML content of the page
        soup = BeautifulSoup(response.content, 'html.parser')

        region_element = soup.find('div', class_='infobox-cell-2 infobox-description', string='Region:')

        if region_element:
            region_text = region_element.find_next_sibling().text.strip()
            if region_text == "North America":
                region_text = "NA"
                return region_text
            elif region_text == "Europe":
                region_text = "EU"
                return region_text
            elif region_text == "South America":
                region_text = "SAM"
                return region_text
            elif region_text == "Asia-Pacific":
                region_text = "APAC"
                return region_text
            elif region_text == "Oceania":
                region_text = "OCE"
                return region_text  
            elif region_text == "MENA":
                return region_text
            else:
                print(f'{region_text} is unsupported')
                return None  
        else:
            print('Failed to retrieve region information from team page')
            return None
    else:
        print('Failed to retrieve team information from Liquipedia')
        return None


def get_pro_player_info(player_name):
    player_url = f'https://liquipedia.net/rocketleague/{player_name}'

    if player_url in cache:
        cached_data = cache[player_url]
        if time.time() - cached_data['timestamp'] < expiration_time:
            # Return cached player information if it's still valid
            return cached_data['team_name'], cached_data['player_age'], cached_data['team_logo'], cached_data['team_region']

    team_name = extract_team_name(player_url)
    player_age = extract_age(get_player_age(player_url))
    team_logo = extract_team_logo(team_name)
    team_region = extract_team_region(team_name)

    if team_name is None or player_age is None or team_logo is None or team_region is None:
        print(f'Failed on {player_name}')
        return None, None, None, None
    else:
        # Update cache with new information
        cache[player_url] = {
            'team_name': team_name,
            'player_age': player_age,
            'team_logo': team_logo,
            'team_region': team_region,
            'timestamp': time.time()
        }

        # Save cache data to file
        with open(cache_file, 'wb') as f:
            pickle.dump(cache, f)


        # In order to avoid rate limiting, if we are forced to update the player information from Liquipedia wait 10 seconds. Any lower WILL GET BANNED
        time.sleep(10)
        return team_name, player_age, team_logo, team_region
