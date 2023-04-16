from flask import Flask,render_template,request,session,redirect,Response,jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField,BooleanField
from wtforms.validators import DataRequired
from httplib2 import Http
from oauth2client.client import OAuth2WebServerFlow, FlowExchangeError
import config
import urllib.parse
import threading,pickle
from utilities.gdrive_utils import GoogleDrive
from loggerfile import logger
import json,os,random

with open('/tmp/progress.json', 'w') as f:
    f.write("{}")


INSERTION_LOCK = threading.RLock()
PROGRESS_FILE = "/tmp/progress.json"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'extrasupresecretkeynooneshouldknow'


class AuthoriseForm(FlaskForm):
    copied_url = StringField("Enter the copied url here",validators=[DataRequired()])
    submit = SubmitField("Submit")


class CloneForm(FlaskForm):
    source_file_url = StringField("What is the source file/folder url",validators=[DataRequired()])
    destination_file_url = StringField("What is the destination file/folder url",validators=[DataRequired()])
    my_checkbox = BooleanField('USE-SA')
    submit = SubmitField("Clone")



class CloningThread(threading.Thread):
    def __init__(self,source_file_url,destination_file_url,creds,clone_id,use_sa):
        self.source = source_file_url
        self.dest = destination_file_url
        self.creds = creds
        self.clone_id = clone_id
        self.use_sa=use_sa
        super().__init__()

    def run(self):
        print('starting')
        gdcls = GoogleDrive(self.source,self.dest,self.creds,self.clone_id,self.use_sa)
        gdcls.clone()



@app.route('/',methods=['GET','POST'])
async def home():
    if 'creds' in session:
        return redirect('/clone')
    
    
    OAUTH_SCOPE = "https://www.googleapis.com/auth/drive"
    # REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
    REDIRECT_URI = "http://localhost:1/"

    flow = OAuth2WebServerFlow(
                                config.G_DRIVE_CLIENT_ID,
                                config.G_DRIVE_CLIENT_SECRET,
                                OAUTH_SCOPE,
                                redirect_uri=REDIRECT_URI
                        )
    auth_url = flow.step1_get_authorize_url()


    copied_url = None
    form = AuthoriseForm()

    if form.validate_on_submit():
        copied_url = form.copied_url.data
        form.copied_url.data = ''
        try:
            query = urllib.parse.urlparse(copied_url).query
            code = urllib.parse.parse_qs(query)['code'][0]
            creds = flow.step2_exchange(code)
            with INSERTION_LOCK:
                session['creds'] = pickle.dumps(creds)
            flow=None
            return redirect('/clone')
    
        except FlowExchangeError as e:
            return render_template('error.html',title="❗ Invalid Code", description=f"The code you have sent is invalid or already used before. Generate new one by the Authorization URL on homepage....\n\n{e}")
        except KeyError:
            return redirect('/')
        except Exception as e:
            return f"Error \n\n {e}"


    return render_template('index.html',auth_url=auth_url, copied_url=copied_url,form=form)

@app.context_processor
def to_pass_to_clonepage():
    form = CloneForm()
    return dict(form=form)

@app.route('/clone')
async def clonepage():
    if not 'creds' in session:
        return redirect('/')
    
    return render_template('clonepage.html',group_email = config.service_acc_google_group_email)



@app.route('/processclone',methods=['POST','GET'])
async def processclone():
    if not 'creds' in session:
        return redirect('/')
    if request.method == 'GET':return redirect('/')

    form = CloneForm()
    if form.validate_on_submit():
        source_file_url = form.source_file_url.data
        destination_file_url = form.destination_file_url.data

        use_sa = form.my_checkbox.data


        with INSERTION_LOCK:
            creds = pickle.loads(session['creds'])

        clone_id = random.randint(10000,99999)

        my_clone = CloningThread(source_file_url,destination_file_url,creds,clone_id,use_sa)

        my_clone.start()

        return jsonify({'clone_id':clone_id})
    else:
        return redirect('/')


@app.route('/progresscheck',methods=['GET','POST'])
async def progcheck():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            progress = json.load(f)
    else:
        progress = {}

    # Handle GET requests
    if request.method == 'GET':
        clone_id = str(request.args.get('clone_id'))
        prog = progress.get(clone_id)
        if not prog:
            prog = {'wait':True}
        return jsonify(prog)

    # Handle POST requests
    elif request.method == 'POST':
        # Replace the progress data with the new data
        new_progress = request.get_json()
            
        if new_progress.get('clearprogress'):
            if os.path.exists(PROGRESS_FILE):
                with open(PROGRESS_FILE, 'w') as f:
                    f.write("{}")
            return "", 202
        else:
            clone_id = str(new_progress.get('clone_id'))
            progress[clone_id] = new_progress

            # Save the updated progress data to the file
            with open(PROGRESS_FILE, "w") as f:
                json.dump(progress, f)

            # Return a response
            return "", 202


@app.route('/sainfo')
async def sainfo():
    return render_template('user_sainfo.html')

if __name__ == '__main__':
    app.run(port=5200,debug=True)