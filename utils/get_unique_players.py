import os
import csv

def get_unique_players():
    player_set = set()

    for file_name in os.listdir('ranked-replays'):
        if file_name.endswith('.csv'):
            with open(os.path.join('ranked-replays', file_name), newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=';', quotechar='|')
                next(reader)  # skip header row
                for row in reader:
                    player_set.add(row[2])

    player_list = sorted(list(player_set))
    return player_list