#!/usr/bin/python3
import RPi.GPIO as GPIO
import time,sys, datetime
import psycopg2
from psycopg2.extras import execute_values
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.header import Header

'''
Configure raspberry
'''

GPIO.setmode(GPIO.BCM)
inpt = 22
GPIO.setup(inpt,GPIO.IN)

'''
Configure some global variables
'''

current_input = GPIO.input(inpt)                        # This is used to compare to the new_input later.
total_rotations = 0                                     # This is a counter. It gets reset after the number of seconds in rotation_downtime.
cup_movements = 132                                     # This is how many rotations occur as a cup of liquid passes through.
rotation_downtime = 5                                   # Sets the cut-off time for establishing a water-flow event.
last_movement_time = time.time() + rotation_downtime    # This is used to determine if a new water-flow event should be created.
record_data = False                                     # A flag used to trigger database insert.
flow_starttime = datetime.datetime.now()
alarm_delta = 900                                       # 15 minutes per alarm 
alarm_time = flow_starttime + datetime.timedelta(seconds=alarm_delta)
data = []

'''
Enter database credentials here. I recommend the free-tier Postgres databses from Heroku which allow up to 10k rows. heroku.com
But you can use any Postgres db - have not tested with other dbs.
'''

host="localhost"
database="water"
user="xxxx" 
password="xxxxxxxx"

print('Control C to exit')
############################################################
#############    Alarm Email Routine    ####################
############################################################
def sendalarm(flowstart, rotations):
    print ("\nALARM!!!!!! at ",datetime.datetime.now())
    smtp_ssl_host = 'smtp.gmail.com'  # smtp.mail.yahoo.com
    smtp_ssl_port = 465
    username = 'xxxxxxxx@gmail.com'          #to use gmail you must allow less secure apps
    password = 'xxxxxx!'
    sender = 'xxxxxxxx@gmail.com'
    targets = ['yyyyy@gmail.com', 'zzzzz@yahoo.com']

    msg = MIMEMultipart()
    msg['Subject'] = Header( 'ALARM for excessive water flow time' , 'utf-8')
    msg['From'] = sender 
    msg['To'] = ', '.join(targets)
    duration = datetime.datetime.now() - flowstart
    tuple1 = ("ALARM for long duration flow",
           "\nFlow started at ", flowstart,
           "\nCurrent time    ", datetime.datetime.now(),
           "\nFlow duration so far ", duration ,
           "\nRotations so far ", rotations)
    body = ''
    for item in tuple1:
        body = body + str(item)
    msg.attach(MIMEText(body, 'plain'))
    server = smtplib.SMTP_SSL(smtp_ssl_host, smtp_ssl_port)
    server.login(username, password)
    server.sendmail(sender, targets, msg.as_string())
    print("Email Sent!")
    #print("time = ", datetime.datetime.now())   #about 2 seconds per email misses some flow 
    server.quit()
##############################################################
##################    Calibration routine    #################    
##############################################################
def calibrate(factorin, cupsout):

    if factorin <= 10:
     cupsout = 110
    elif factorin <= 45:                      # Kitchen sink
     cupsout = 138
    elif factorin > 200:
     cupsout  =  138                          # pool file raw rainwater feed
    elif factorin > 135:
     cupsout  =  145
    elif factorin >= 80:
     cupsout = 144                            # rear rainwater hosebib
    else:
     cupsout = 142                            # master shower 
    return(cupsout)
###############################################################
####################    insert to database    #################
###############################################################
def commit_data(conn, data):

    '''
    This passes data to the data base as a single row. It then resets/empties data.
    '''

    cur = conn.cursor()
    insert_statement = "INSERT INTO flow_meter ('datetime','movements','cups','gallons','flow_starttime',realgals') VALUES %s".replace("'",'')
    execute_values(cur,insert_statement,data)
    conn.commit()
    print ('Data sent.\n')
    cur.close()
    data = []
    return data
##################################################################
#######################    prepare data routine   ################
##################################################################
def prep_and_send(data,total_rotations):

    '''
    Calculates measurements (cups and gallons). Prepares the data into a database-friendly tuple. Appends that tuple to a list. 
    
    It then tries to connect to database. If it is not successful then it does nothing but saves the data; it will try to send 
    the list of data-tuples the next time there is a water-flow event. 
    
    Once the connection is successful data is emptied in commit_data().
    '''

    global cup_movements
    now = datetime.datetime.now() 
    diff = now - flow_starttime                      # how long was it flowing
    flow_time = diff.seconds                         # 
###
    factorin = total_rotations / flow_time                     #a proxy for liters/minute most meters curve is like f=(5.5*liters/min)
    cup_movements = calibrate(factorin,cup_movements)
###
    print("\n factorin and Cup_movements = ", factorin, " ", cup_movements)
    total_cups = total_rotations/cup_movements
    total_gallons = total_cups/16
    print (' Flow Time', flow_time) 
    print(' {}:\n Movements: {}. \n Cups: {}. \n Gallons: {}'.format(now,total_rotations,total_cups,total_gallons))

    current_data = (
        now,
        round(total_rotations,2),
        round(total_cups,2),
        round(total_gallons,2),
        flow_starttime,
        total_gallons, 
        )
    data.append(current_data)
    try:                
        '''
        Establish connection with Db and try to insert.
        '''
        conn = psycopg2.connect(host = host, database = database, user = user, password = password)
        data = commit_data(conn, data)          
        conn.close()
    except psycopg2.OperationalError as e:
        '''In case of error does not reset data to [] (see commit_data).'''
        e = e + '\n' + e.__traceback__
        print (e)                      
    return data
##############################################################################
##############################   Main Code    ################################
##############################################################################
while True:

    '''
    This is what actually runs the whole time. 
    It first checks to see if new_input is different from current_input. This would be the case if there was a rotation.
    Once it detects that the input is different it knows water is flowing.
    It starts tracking the total_rotations and when the last rotation occured. 
    After each rotation it refreshes the value of the last rotation time.
    It waits a few seconds (rotation_downtime) after the last rotation time to make sure the water has stopped. 
    Once the water stops it passes the total_rotations to prep_and_send(). 
    It also passes 'data' which is any previous water-flow events that were not successfully sent at the time they were recorded.
    '''
    if (record_data == False):
        time.sleep(0.1)                #this little wait only happens when no flow.
                                       #Might miss the first insignificant 100 miliseconds
                                       # but it reduces CPU usage from 97% to about 1% (pi zero) and heat by 10C
        alarm_time = datetime.datetime.now() + datetime.timedelta(seconds=alarm_delta)   #reset alarm
    else: None 
    new_input = GPIO.input(inpt)              
    if new_input != current_input:
        total_rotations += 1
        if time.time() <= last_movement_time:                                         #flow continues if it hasn't been more than X seconds
            record_data = True
            current_input = new_input
            last_movement_time = time.time() + rotation_downtime
        else:                                                                             #flow starts
            last_movement_time = time.time() + rotation_downtime
            flow_starttime = datetime.datetime.now()
            alarm_time = flow_starttime + datetime.timedelta(seconds=alarm_delta)         #set initial alarm 
            print("Flow starts at ", flow_starttime)
    elif record_data == True and time.time() > last_movement_time: #if it's been X seconds since last change flow is over
        print("Flow Stops  at ", datetime.datetime.now())
        data = prep_and_send(data,total_rotations)
        record_data = False
        total_rotations = 0
        last_movement_time = time.time() + rotation_downtime
        alarm_time = datetime.datetime.now() + datetime.timedelta(seconds=alarm_delta)      #reset alarm
        current_input = new_input
    if  ((alarm_time < datetime.datetime.now()) and (record_data == True)):                 # past time for alarm? 
        sendalarm(flow_starttime, total_rotations)                                          # send an alarm
        alarm_time = alarm_time + datetime.timedelta(seconds=alarm_delta)                   # set time for next alarm
##############################################################################
'''
This last part simply prints some helpful information. It also allows for a clean exit if user presses Ctrl + C.
'''
try:
    print('New input: ',new_input, '. Current input: ', current_input, '. Movements: ', total_rotations)
except KeyboardInterrupt:
    print('\nCTRL C - Exiting nicely')
    GPIO.cleanup()
    sys.exit()
