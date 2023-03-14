import os
import requests
import time

def download_replays(rank, playlist, count):
    import utils.get_rank_replay_ids as get_rank_replay_ids

    replay_ids = get_rank_replay_ids.get_rank_replay_ids(rank, playlist, count)
    base_url = "https://ballchasing.com/dl/stats/players/"

    # create replays folder if it doesn't exist
    if not os.path.exists("ranked-replays"):
        os.makedirs("ranked-replays")

    # download files
    last_msg_length = 0
    for i, replay_id in enumerate(replay_ids):
        url = base_url + replay_id + "/" + replay_id + "-players.csv"
        response = requests.get(url)
        num_attempts = 1
        while response.status_code != 200 and num_attempts <= 5:
            msg = f"Failed to download replay with ID {replay_id}. Retrying in {num_attempts} seconds..."
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(num_attempts)
            response = requests.get(url)
            num_attempts += 1
        if response.status_code == 200:
            with open(f"ranked-replays/{replay_id}.csv", "wb") as f:
                f.write(response.content)
            msg = f"Downloaded replay {i+1}/{count} ({int(((i+1)/count)*100)}%) with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(1.0)
        else:
            msg = f"Failed to download replay with ID {replay_id}"
            if len(msg) < last_msg_length:
                msg += " " * (last_msg_length - len(msg))
            last_msg_length = len(msg)
            print(msg, end="\r")
            time.sleep(10.0)

    print(f"All {count} replays downloaded")


def print_menu():
    print("Please select a rank:")
    print("1. Platinum 1")
    print("2. Platinum 2")
    print("3. Platinum 3")
    print("4. Diamond 1")
    print("5. Diamond 2")
    print("6. Diamond 3")
    print("7. Champion 1")
    print("8. Champion 2")
    print("9. Champion 3")

def print_playlist_menu():
    print("Please select a playlist:")
    print("1. Duel")
    print("2. Doubles")
    print("3. Standard")

def print_downloader_choice_menu():
    print("Do you want to run the Sample Pool Downloader after?:")
    print("1. Yes")
    print("2. No")

def get_rank_choice():
    while True:
        try:
            choice = int(input("Enter choice [1-9]: "))
            if 1 <= choice <= 9:
                return choice
            else:
                print("Invalid choice. Please enter a number between 1 and 9.")
        except ValueError:
            print("Invalid choice. Please enter a number between 1 and 9.")

def get_playlist_choice():
    while True:
        try:
            choice = int(input("Enter choice [1-3]: "))
            if 1 <= choice <= 3:
                return choice
            else:
                print("Invalid choice. Please enter a number between 1 and 3.")
        except ValueError:
            print("Invalid choice. Please enter a number between 1 and 3.")
        
def get_count_choice():
    while True:
        count = input("Enter the number of replays you want to download (1-200): ")
        try:
            count = int(count)
            if count < 1 or count > 200:
                print("Invalid input, please enter a number between 1 and 200.")
            else:
                return count
        except ValueError:
            print("Invalid input, please enter a number between 1 and 200.")

def get_downloader_choice():
    while True:
        try:
            choice = int(input("Enter choice [1-2]: "))
            if 1 <= choice <= 3:
                return choice
            else:
                print("Invalid choice. Please enter a number between 1 and 2.")
        except ValueError:
            print("Invalid choice. Please enter a number between 1 and 2.")

def main():
    while True:
        print_menu()
        rank_choice = get_rank_choice()

        rank = ""
        if rank_choice == 1:
            rank = "platinum-1"
        elif rank_choice == 2:
            rank = "platinum-2"
        elif rank_choice == 3:
            rank = "platinum-3"
        elif rank_choice == 4:
            rank = "diamond-1"
        elif rank_choice == 5:
            rank = "diamond-2"
        elif rank_choice == 6:
            rank = "diamond-3"
        elif rank_choice == 7:
            rank = "champion-1"
        elif rank_choice == 8:
            rank = "champion-2"
        elif rank_choice == 9:
            rank = "champion-3"
        

        print_playlist_menu()
        playlist_choice = get_playlist_choice()

        if playlist_choice == 1:
            playlist = "ranked-duels"
        elif playlist_choice == 2:
            playlist = "ranked-doubles"
        elif playlist_choice == 3:
            playlist = "ranked-standard"

        
        count = get_count_choice()

        print_downloader_choice_menu()
        downloader_choice = get_downloader_choice()

        if downloader_choice == 1:
            run_downloader =  True
        elif downloader_choice == 2:
            run_downloader = False

        print(f"Downloading {count} random replays of {rank} {playlist}")
        download_replays(rank, playlist, count)
        if run_downloader:
            import sample_pool_downloader
        break

main()