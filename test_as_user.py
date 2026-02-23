import sys
import urllib.request
import json

TOKEN = "xoxp-4131935301158-4138553919170-10541136382135-3e53106a1f95de8ae377e543affa0bb0"
CHANNEL = "C0AEZ241C3V"
BOT_ID = "U0AG8LVAB4M" # Lucy's user ID

def post_message(text):
    data = json.dumps({
        'channel': CHANNEL,
        'text': f"<@{BOT_ID}> {text}",
        'as_user': True
    }).encode('utf-8')
    
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=data,
        headers={
            'Authorization': f'Bearer {TOKEN}',
            'Content-Type': 'application/json'
        }
    )
    
    response = urllib.request.urlopen(req).read().decode('utf-8')
    return json.loads(response)

if __name__ == "__main__":
    msg = "Hi Lucy! I am Ojash. I want to test your integrations. Can you check what tools or integrations I have connected? I just want to see how you respond."
    if len(sys.argv) > 1:
        msg = sys.argv[1]
    
    print(f"Sending message as user: {msg}")
    res = post_message(msg)
    print(json.dumps(res, indent=2))
