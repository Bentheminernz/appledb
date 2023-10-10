from pathlib import Path
import re
import argparse
import base64
import json
import requests
import urllib3

# Disable SSL warnings, because Apple's SSL is broken
urllib3.disable_warnings()

session = requests.session()

# Some builds show up as prerequisites in the zip file, but don't give a response from Pallas in some cases; skip those
skip_builds = {
    '19A340': [],
    '19A344': [],
    '19B74': [
        'iPad14,1'
    ],
    '19C56': [
        'iPhone14,2',
        'iPhone14,3',
        'iPhone14,4',
        'iPhone14,5'
    ]
}

asset_audiences_overrides = {
    'iPadOS': 'iOS'
}

asset_audiences = {
    'iOS': {
        'beta': '9dcdaf87-801d-42f6-8ec6-307bd2ab9955',
        'release': '01c1d682-6e8f-4908-b724-5501fe3f5e5c',
        'security': 'c724cb61-e974-42d3-a911-ffd4dce11eda'
    },
    'macOS': {
        '12beta': '298e518d-b45e-4d36-94be-34a63d6777ec',
        '13beta': '683e9586-8a82-4e5f-b0e7-767541864b8b',
        '14beta': '77c3bd36-d384-44e8-b550-05122d7da438',
        'release': '60b55e25-a8ed-4f45-826c-c1495a4ccc65'
    },
    'tvOS': {
        'beta': '61693fed-ab18-49f3-8983-7c3adf843913',
        'release': '356d9da0-eee4-4c6c-bbe5-99b60eadddf0'
    },
    'watchOS': {
        'beta': '7ae7f3b9-886a-437f-9b22-e9f017431b0e',
        'release': 'b82fcf9c-c284-41c9-8eb2-e69bf5a5269f'
    },
    'audioOS': {
        'beta': '17536d4c-1a9d-4169-bc62-920a3873f7a5',
        'release': '0322d49d-d558-4ddf-bdff-c0443d0e6fac'
    },
    'visionOS': {
        'release': 'c59ff9d1-5468-4f6c-9e54-f68d5eeab93b'
    },
    'Studio Display Firmware': {
        'release': '02d8e57e-dd1c-4090-aa50-b4ed2aef0062'
    }
}

parser = argparse.ArgumentParser()
parser.add_argument('-o', '--os', required=True, action='append', choices=['audioOS', 'iOS', 'iPadOS', 'macOS', 'tvOS', 'visionOS', 'watchOS', 'Studio Display Firmware'])
parser.add_argument('-b', '--build', required=True, action='append', nargs='+')
parser.add_argument('-a', '--audience', default='release')
parser.add_argument('-r', '--rsr', action='store_true')
parser.add_argument('-d', '--devices', nargs='+')
args = parser.parse_args()

parsed_args = dict(zip(args.os, args.build))

board_ids = {}
build_versions = {}

def get_board_ids(identifier):
    global board_ids
    if not board_ids.get(identifier):
        device_path = list(Path('deviceFiles').rglob(f"{identifier}.json"))[0]
        device_data = json.load(device_path.open())
        
        if device_data.get('iBridge'):
            device_path = Path(f"deviceFiles/iBridge/{device_data['iBridge']}.json")
            device_data = json.load(device_path.open())
        if isinstance(device_data['board'], list):
            board_ids[identifier] = device_data['board']
        else:
            board_ids[identifier] = [device_data['board']]
    return board_ids[identifier]

def get_build_version(osStr, build):
    global build_versions
    if not build_versions.get(f"{osStr}-{build}"):
        build_path = list(Path(f'osFiles/{osStr}').rglob(f'{build}.json'))[0]
        build_data = json.load(build_path.open())
        build_versions[f"{osStr}-{build}"] = build_data['version'].split(' ')[0]

    return build_versions[f"{osStr}-{build}"]

def call_pallas(device_name, board_id, os_version, os_build, osStr, audience, is_rsr):
    asset_type = 'SoftwareUpdate'
    if is_rsr:
        asset_type = 'Splat' + asset_type
    if osStr == 'macOS':
        asset_type = 'Mac' + asset_type
    
    if osStr == 'Studio Display Firmware':
        asset_type = 'DarwinAccessoryUpdate.A2525'

    # iOS and iPadOS use the same asset audiences
    # Allow for someone to pass in a specific asset audience UUID
    asset_audience = asset_audiences[asset_audiences_overrides.get(osStr, osStr)].get(audience, audience)

    request = {
        "ClientVersion": 2,
        "AssetType": f"com.apple.MobileAsset.{asset_type}",
        "AssetAudience": asset_audience,
        # Device name might have an AppleDB-specific suffix; remove this when calling Pallas
        "ProductType": device_name.split("-")[0],
        "HWModelStr": board_id,
        "ProductVersion": os_version,
        "Build": os_build,
        "BuildVersion": os_build
    }
    if is_rsr:
        request['RestoreVersion'] = '0.0.0.0.0,0'

    if str(build[-1]).islower() and osStr in ['audioOS', 'iOS', 'iPadOS', 'tvOS', 'visionOS']:
        request['ReleaseType'] = 'Beta'

    response = session.post("https://gdmf.apple.com/v2/assets", json=request, headers={"Content-Type": "application/json"}, verify=False)

    parsed_response = json.loads(base64.b64decode(response.text.split('.')[1] + '==', validate=False))
    assets = parsed_response.get('Assets', [])
    links = set()
    for asset in assets:
        links.add(f"{asset['__BaseURL']}{asset['__RelativePath']}")
    return links

ota_links = set()
for (osStr, builds) in parsed_args.items():
    print(f"Checking {osStr}")
    for build in builds:
        print(f"\tChecking {build}")
        kern_version = re.search(r"\d+(?=[a-zA-Z])", build)
        assert kern_version
        kern_version = kern_version.group()
        if osStr == 'visionOS':
            build_path = Path(f"osFiles/{osStr}/{build}.json")
        else:
            build_path = list(Path(f"osFiles/{osStr}").glob(f"{kern_version}x*"))[0].joinpath(f"{build}.json")
        devices = {}
        build_data = {}
        try:
            build_data = json.load(build_path.open())
        except:
            print(f"Bad path - {build_path}")
            continue
        for device in build_data['deviceMap']:
            if args.devices and device not in args.devices:
                continue
            devices.setdefault(device, {
                'boards': get_board_ids(device),
                'builds': {}
            })

        # RSRs are only for the latest version
        if not args.rsr:
            for source in build_data.get("sources", []):
                if not source.get('prerequisiteBuild'):
                    continue

                current_device = source['deviceMap'][-1]
                if args.devices and current_device not in args.devices:
                    continue

                prerequisite_builds = source['prerequisiteBuild']
                if isinstance(prerequisite_builds, list):
                    for prerequisite_build_option in prerequisite_builds:
                        if skip_builds.get(prerequisite_build_option) is not None:
                            if len(skip_builds[prerequisite_build_option]) == 0 or current_device in skip_builds[prerequisite_build_option]:
                                continue
                        prerequisite_build = prerequisite_build_option
                        break
                else:
                    prerequisite_build = prerequisite_builds

                devices[current_device]['builds'][prerequisite_build] = get_build_version(osStr, prerequisite_build)

        for key, value in devices.items():
            for board in value['boards']:
                for prerequisite_build, version in value['builds'].items():
                    ota_links.update(call_pallas(key, board, version, prerequisite_build, osStr, args.audience, args.rsr))
                ota_links.update(call_pallas(key, board, build_data['version'].split(' ')[0], build, osStr, args.audience, args.rsr))

[i.unlink() for i in Path.cwd().glob("import-ota") if i.is_file()]
Path("import-ota.txt").write_text("\n".join(sorted(ota_links)), "utf-8")