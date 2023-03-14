import os
import csv

import os
import csv

def calculate_defensive_war(row):
    goals_conceded_last_defender = float(row[15])
    saves = float(row[11])
    shots_conceded = float(row[13])
    pct_defensive_third = float(row[65])
    demos_taken = float(row[71])
    percentage_in_front_of_ball = float(row[59])
    avg_distance_to_ball_no_possession = float(row[55])
    time_most_back = float(row[51])

    # calculate defensive_war
    defensive_war = (goals_conceded_last_defender * -3.5) + \
                    (saves * 3.5) + (shots_conceded * -0.75) + \
                    (demos_taken * -0.5) + (percentage_in_front_of_ball * -0.15) + \
                    (avg_distance_to_ball_no_possession * -0.001) + (time_most_back * 0.10)

    # divide defensive_war by pct_defensive_third
    defensive_war = defensive_war / (pct_defensive_third)

    return defensive_war

def calculate_avg_defensive_stats(player_name):
    total_saves = 0
    total_shots_conceded = 0
    num_games = 0
    
    for file_name in os.listdir('replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                
                for row in reader:
                    if row[2] == player_name:
                        saves = float(row[11])
                        shots_conceded = float(row[13])
                        
                        total_saves += saves
                        total_shots_conceded += shots_conceded
                        
                        num_games += 1
                        
    if num_games == 0:
        return 0, 0, 0
    
    avg_saves = total_saves / num_games
    avg_shots_conceded = total_shots_conceded / num_games
    
    return avg_saves, avg_shots_conceded, num_games

def calculate_average_defensive_war(player_name):
    total_war = 0
    num_games = 0
    
    for file_name in os.listdir('replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                
                for row in reader:
                    if row[2] == player_name:
                        defensive_war = calculate_defensive_war(row)
                        total_war += defensive_war
                        num_games += 1
                        
    if num_games == 0:
        return 0, 0
    
    avg_war = total_war / num_games
    return avg_war, num_games