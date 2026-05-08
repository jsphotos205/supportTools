# supportTools

Internal Python tooling project for developing utilities that help automate and speed up Support workflows.

Current workflow integrations and targets:

- HelpScout (Support ticket workflows)
- Linear (bug reporting and QA formatting)
- Slack (internal communication)
- Google Chrome download workflows
- Topaz `.tzlog` troubleshooting analysis

The repository is managed with Git and GitHub for version control and iterative development.

# **tzlog_reader.py**

Current support utility for parsing Topaz `.tzlog` files.

# **Current tzlog Reader Workflow**

## **Step 1**

Support downloads:

* `.tar`
* `.tar.gz`
* `.tgz`
* `.zip`

files into:

```bash
testFolder/
```

## **Step 2**

Run the script:

```bash
testFolder/
```

## **Step 3**

The script automatically extract supported archive creates the `extracted_tzlog_archives/` and locates .tzlog files while parsing the first valid log containing system information

## **Step 4**

The script gathers:

* User OS
* Topaz Photo version
* OS
* CPU
* RAM
* Machine ID
* Device count
* Indexed GPUs
* GPU VRAM

This information is automatically copied to the clipboard.

## **Step 5**

Support pastes the copied system information into the HelpScout ticket or Linear task.

## **Step 6**

The script asks for the issue-specific `.tzlog` file.

## **Step 7**

The script gathers:

* `.tzlog` filename
* Crashpad session ID

The final report is copied to the clipboard for Linear bug reporting.

# **Example Output**

```
System information:
  User OS: macOS
  Topaz Photo version: 2.0.1
  OS: macOS 15.5
  CPU: Apple M4 Max
  RAM: 64 GB
  Machine ID: XXXXX
  Device count: 2
  Indexed GPUs:
    - Index 0: Apple M4 Max | VRAM: Unified

Issue log information:
  Log file: TopazPhoto_2026_05_08_10_15_32.tzlog
  Crashpad session ID: XXXXXXXX
```

---

# Current Project Structure

```text
supportTools/
├── README.md
├── .gitignore
├── requirements.txt
├── venv/
├── tools/
│   └── tzLogReader/
│       └── tzlog_reader.py
└── testFolder/
```

# **Environment Setup**

## **Create the Project Folder**

```bash
mkdir ~/Projects/supportTools
cd ~/Projects/supportTools
```

Open in VS Code:

```bash
code .
```

# **Python Virtual Environment**

## **Create Virtual Environment**

```bash
python3 -m venv venv
```

## **Activate Virtual Environment**

```bash
source venv/bin/activate
```

Expected terminal prompt:

```bash
(venv)
```

If `(base)` also appears:

```bash
conda deactivate
```


# **tzlog_reader.py**

Current support utility for parsing Topaz `.tzlog` files.

Location:
