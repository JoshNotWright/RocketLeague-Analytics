import os
import csv

def calculate_offensive_war(row):
    goals = float(row[9])
    assists = float(row[10])
    shots = float(row[12])
    shot_acc = float(row[16])
    avg_boost = float(row[18])
    time_most_forward = float(row[52])
    pct_offensive_third = float(row[69])
    demos_inflicted = float(row[70])

    """     # print out the non-weighted values
    print("\nNon-weighted values:")
    print(f"goals: {goals}")
    print(f"assists: {assists}")
    print(f"shots: {shots}")
    print(f"shot_acc: {shot_acc}")
    print(f"avg_boost: {avg_boost}")
    print(f"time_most_forward: {time_most_forward}")
    print(f"pct_offensive_third: {pct_offensive_third}")
    print(f"demos_inflicted: {demos_inflicted}")

    # print out the weighted values
    print("\nWeighted values:")
    print(f"goals: {goals * 4}")
    print(f"assists: {assists * 3}")
    print(f"shots: {(shots * 0.05) + (shot_acc * 0.2)}")
    print(f"avg_boost: {avg_boost * 0.01}")
    print(f"time_most_forward: {time_most_forward * 0.02}")
    print(f"demos_inflicted: {demos_inflicted * 0.5}") """

    # check if pct_offensive_third is zero
    if pct_offensive_third == 0:
        return 0

    # calculate offensive_war
    offensive_war = (goals * 5) + (assists * 4) + ((shots * 0.05) + (shot_acc * 0.2)) + \
                (time_most_forward * 0.02) + (demos_inflicted * 0.5)
    
    """ # print out the offensive_war before division
    print(f"\Offensive War (before division): {offensive_war}") """


    # divide offensive by pct_offensive_third
    offensive_war = (offensive_war / (pct_offensive_third)) + (avg_boost * 0.01)

    """ # print out the offensive_war after division
    print(f"\Offensive War (after division): {offensive_war}") """

    return offensive_war




def calculate_avg_offensive_stats(player_name):
    total_goals = 0
    total_assists = 0
    total_shot_acc = 0
    total_shots = 0
    total_avg_boost = 0
    total_demos_inflicted = 0
    num_games = 0
    
    for file_name in os.listdir('replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                
                for row in reader:
                    if row[2] == player_name:
                        goals = float(row[9])
                        assists = float(row[10])
                        shot_acc = float(row[16])
                        shots = float(row[12])
                        avg_boost = float(row[18])
                        demos_inflicted = float(row[70])
                        
                        total_goals += goals
                        total_assists += assists
                        total_shot_acc += shot_acc
                        total_avg_boost += avg_boost
                        total_demos_inflicted += demos_inflicted
                        total_shots += shots
                        
                        num_games += 1
                        
    if num_games == 0:
        return 0, 0, 0, 0, 0, 0, 0
    
    avg_goals = total_goals / num_games
    avg_assists = total_assists / num_games
    avg_shot_acc = total_shot_acc / num_games
    avg_avg_boost = total_avg_boost / num_games
    avg_demos_inflicted = total_demos_inflicted / num_games
    avg_shots = total_shots / num_games
    
    return avg_goals, avg_assists, avg_shot_acc, avg_avg_boost, avg_demos_inflicted, avg_shots, num_games

def calculate_average_offensive_war(player_name):
    total_war = 0
    num_games = 0
    
    for file_name in os.listdir('replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                
                for row in reader:
                    if row[2] == player_name:
                        offensive_war = calculate_offensive_war(row)
                        total_war += offensive_war
                        num_games += 1
                        
    if num_games == 0:
        return 0, 0
    
    avg_war = total_war / num_games
    return avg_war, num_games