import random
import time
limit = 10000000

i = 0

number = 1
goal_times_in_a_row = 7
times_in_a_row = 0

def roll(number, times_in_a_row):
    if random.randint(1,6) == number:
        times_in_a_row += 1
        return times_in_a_row
    else:
        times_in_a_row = 0
        return times_in_a_row
        
    


while i < limit and times_in_a_row < goal_times_in_a_row:
    times_in_a_row = roll(number, times_in_a_row)

    i += 1

print(f"it took {i} rolls to get {goal_times_in_a_row} {number}'s in a row")