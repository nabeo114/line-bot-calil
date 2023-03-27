import os
import sys
import logging
import json

import base64
import hashlib
import hmac

import urllib.request, urllib.parse

import re
import time
import boto3
import decimal

import numpy as np
import cv2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# get channel_secret and channel_access_token from your environment variable
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
if channel_secret is None:
    logger.error('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    logger.error('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

dynamodb = boto3.resource('dynamodb', region_name=os.environ['Region'])
table = dynamodb.Table(os.environ['TableName'])

bd = cv2.barcode.BarcodeDetector()

max_library = 8

logger.info(sys.path)

def lambda_handler(event, context):
    logger.info(json.dumps(event))
    
    body = event.get('body', '')  # Request body string
    hash = hmac.new(channel_secret.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
    signature = base64.b64encode(hash).decode('utf-8')
    # Compare X-Line-Signature request header and the signature
    if signature != event.get('headers').get('X-Line-Signature', '') and signature != event.get('headers').get('x-line-signature', ''):
        logger.error('Validate Error')
        return {'statusCode': 403, 'body': '{}'}
    
    for event_data in json.loads(body).get('events', []):
        if event_data['type'] == 'follow':
            table.put_item(Item = {
                'userId': event_data['source']['userId'],
                'libraries': [],
                'favorites': []
            })
            continue
        elif event_data['type'] == 'message':
            response = table.get_item(Key = {'userId': event_data['source']['userId']})
            favorites = response["Item"]['favorites']
            
            reply_item = [{
                'type': 'action',
                'action': {
                    'type': 'message',
                    'label': 'やめる',
                    'text': 'やめる'
                }
            },
            {
                'type': 'action',
                'action': {
                    'type': 'message',
                    'label': '図書館を探す',
                    'text': '図書館を探す'
                }
            }]
            
            if len(favorites) != 0:
                reply_item.append({
                    'type': 'action',
                    'action': {
                        'type': 'message',
                        'label': '蔵書を探す',
                        'text': '蔵書を探す'
                    }
                })
                reply_item.append({
                    'type': 'action',
                    'action': {
                        'type': 'message',
                        'label': '編集する',
                        'text': '編集する'
                    }
                })
            
            message_body = [{
                'type': 'text',
                'text': 'ご用件は何ですか？',
                'quickReply': {
                    'items': reply_item
                }
            }]
            
            if event_data['message']['type'] == 'location':
                message_lat = event_data['message']['latitude']
                message_lng = event_data['message']['longitude']
                url = 'https://api.calil.jp/library'
                appkey = os.getenv('CALIL_APPKEY', None)
                req = urllib.request.Request(url + '?appkey=' + appkey + '&geocode=' + str(message_lng) + ',' + str(message_lat) + '&format=json&callback=&limit=' + str(max_library))
                with urllib.request.urlopen(req) as res:
                    res_body = res.read()
                    logger.info(res_body)
                    
                    reply_text = ''
                    reply_column = []
                    reply_item = [{
                        'type': 'action',
                        'action': {
                            'type': 'message',
                            'label': 'やめる',
                            'text': 'やめる'
                        }
                    }]
                    
                    for i, library in enumerate(json.loads(res_body)):
                        logger.info(library)
                        reply_text += str(i+1) + '. ' + library['short'] + '\n'
                        reply_column.append({
                            'title': str(i+1) + '. ' + library['short'],
                            'text': library['formal'] + '\n' + library['address'],
                            'defaultAction': {
                                'type': 'uri',
                                'label': '詳細を見る',
                                'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                            },
                            'actions': [{
                                'type': 'uri',
                                'label': '詳細を見る',
                                'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                            }]
                        })
                        reply_item.append({
                            'type': 'action',
                            'action': {
                                'type': 'postback',
                                'label': str(i+1),
                                'data': 'action=add&number=' + str(i+1),
                                'displayText': str(i+1)
                            }
                        })
                    
                    if reply_text == '':
                        message_body = [{
                            'type': 'text',
                            'text': '近くに図書館は無さそうです。'
                        }]
                    else:
                        table.update_item(
                            Key = {'userId': event_data['source']['userId']},
                            UpdateExpression = "set libraries=:l",
                            ExpressionAttributeValues = {
                                ':l': json.loads(res_body, parse_float=decimal.Decimal)
                            },
                            ReturnValues="UPDATED_NEW"
                        )
                        
                        message_body = [{
                            'type': 'text',
                            'text': '近くの図書館をお調べしました。\nお気に入り図書館に登録すると蔵書を検索できます。登録したい図書館の番号を教えて下さい。'
                        }]
                        message_body.append({
                            'type': 'template',
                            'altText': reply_text,
                            'template': {
                                'type': 'carousel',
                                'columns': reply_column
                            },
                            'quickReply': {
                                'items': reply_item
                            }
                        })
            elif event_data['message']['type'] == 'image':
                content_type = event_data['message']['contentProvider']['type']
                
                if content_type == 'line':
                    url = 'https://api-data.line.me/v2/bot/message/'
                    headers = {
                        'Authorization': 'Bearer ' + channel_access_token,
                    }
                    req = urllib.request.Request(url + str(event_data['message']['id']) + '/content', headers=headers)
                    with urllib.request.urlopen(req) as res:
                        res_body = res.read()
                        
                        arr = np.frombuffer(res_body, dtype=np.uint8)
                        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        retval, decoded_info, decoded_type, points = bd.detectAndDecode(img)
                        if retval == True:
                            logger.info(decoded_info)
                            
                            reply_text = ''
                            reply_item = [{
                                'type': 'action',
                                'action': {
                                    'type': 'message',
                                    'label': 'やめる',
                                    'text': 'やめる'
                                }
                            }]
                            
                            for i, code in enumerate(decoded_info):
                                logger.info(code)
                                if code != '':
                                    reply_text += str(i+1) + '. ' + code + '\n'
                                    reply_item.append({
                                        'type': 'action',
                                        'action': {
                                            'type': 'message',
                                            'label': str(i+1),
                                            'text': code
                                        }
                                    })
                            
                            if reply_text == '':
                                message_body = [{
                                    'type': 'text',
                                    'text': 'バーコードを読み取れません。'
                                }]
                            else:
                                message_body = [{
                                    'type': 'text',
                                    'text': 'バーコードを読み取りました。\n' + reply_text + '\n調べたい書籍のISBNを教えて下さい。',
                                    'quickReply': {
                                        'items': reply_item
                                    }
                                }]
                        else:
                            message_body = [{
                                'type': 'text',
                                'text': 'バーコードが見つかりません。'
                            }]
                else:
                    continue
            elif event_data['message']['type'] == 'text':
                message_text = event_data['message']['text']
                valid_isbn = r'^(\d{10}|\d{13})$'
                
                if message_text == 'やめる':
                    remove_all_libraries(event_data)
                    
                    message_body = [{
                        'type': 'text',
                        'text': 'またね。'
                    }]
                elif message_text == '図書館を探す':
                    message_body = [{
                        'type': 'text',
                        'text': '近くの図書館をお調べします。\n位置情報を教えて下さい。',
                        'quickReply': {
                            'items': [{
                                'type': 'action',
                                'action': {
                                    'type': 'message',
                                    'label': 'やめる',
                                    'text': 'やめる'
                                }
                            },
                            {
                                'type': 'action',
                                'action': {
                                    'type': 'location',
                                    'label': '位置情報を送る',
                                }
                            }]
                        }
                    }]
                elif message_text == '蔵書を探す':
                    response = table.get_item(Key = {'userId': event_data['source']['userId']})
                    favorites = response["Item"]['favorites']
                    
                    if len(favorites) == 0:
                        message_body = [{
                            'type': 'text',
                            'text': '蔵書を探すにはお気に入り図書館を登録する必要があります。\n近くの図書館を探しますか？',
                            'quickReply': {
                                'items': [{
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': 'やめる',
                                        'text': 'やめる'
                                    }
                                },
                                {
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': '図書館を探す',
                                        'text': '図書館を探す'
                                    }
                                }]
                            }
                        }]
                    else:
                        reply_text = ''
                        for i, library in enumerate(favorites):
                            logger.info(library)
                            reply_text += str(i+1) + '. ' + library['short'] + '\n'
                        
                        message_body = [{
                            'type': 'text',
                            'text': '以下のお気に入り図書館の蔵書をお調べします。\n' + reply_text + '\n調べたい書籍のISBN(バーコードの画像、もしくは10桁または13桁の数字)を教えて下さい。\n例：9784834000825',
                            'quickReply': {
                                'items': [{
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': 'やめる',
                                        'text': 'やめる'
                                    }
                                },
                                {
                                    'type': 'action',
                                    'action': {
                                        'type': 'camera',
                                        'label': 'カメラを起動する'
                                    }
                                },
                                {
                                    'type': 'action',
                                    'action': {
                                        'type': 'cameraRoll',
                                        'label': 'カメラロールを開く'
                                    }
                                }]
                            }
                        }]
                # ISBN(10桁または13桁の数字)
                elif re.match(valid_isbn, message_text) is not None:
                    response = table.get_item(Key = {'userId': event_data['source']['userId']})
                    favorites = response["Item"]['favorites']
                    
                    if len(favorites) == 0:
                        message_body = [{
                            'type': 'text',
                            'text': '蔵書を探すにはお気に入り図書館を登録する必要があります。\n近くの図書館を探しますか？',
                            'quickReply': {
                                'items': [{
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': 'やめる',
                                        'text': 'やめる'
                                    }
                                },
                                {
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': '図書館を探す',
                                        'text': '図書館を探す'
                                    }
                                }]
                            }
                        }]
                    else:
                        url = 'https://api.calil.jp/check'
                        appkey = os.getenv('CALIL_APPKEY', None)
                        
                        systemids = ''
                        for i, library in enumerate(favorites):
                            logger.info(library)
                            if i == 0:
                                systemids += library['systemid']
                            else:
                                systemids += ',' + library['systemid']
                        logger.info(systemids)
                        
                        req = urllib.request.Request(url + '?appkey=' + appkey + '&isbn=' + message_text + '&systemid=' + systemids + '&format=json&callback=no')
                        with urllib.request.urlopen(req) as res:
                            res_body = res.read()
                            logger.info(res_body)
                            
                            while json.loads(res_body).get('continue', '') != 0:
                                time.sleep(2)
                                req = urllib.request.Request(url + '?appkey=' + appkey +'&session=' + json.loads(res_body).get('session', '') + '&format=json&callback=no')
                                with urllib.request.urlopen(req) as res:
                                    res_body = res.read()
                                    logger.info(res_body)
                            
                            reply_text = ''
                            reply_column = []
                            for i, library in enumerate(favorites):
                                logger.info(library)
                                for libkey in json.loads(res_body).get('books').get(message_text).get(library['systemid']).get('libkey', ''):
                                    logger.info(libkey)
                                    if libkey == library['libkey']:
                                        reply_text += library['short'] + '：' + json.loads(res_body).get('books').get(message_text).get(library['systemid']).get('libkey', '').get(libkey, '') + '\n'
                                        reply_column.append({
                                            'title': '【' + json.loads(res_body).get('books').get(message_text).get(library['systemid']).get('libkey', '').get(libkey, '') + '】' + library['short'],
                                            'text': library['formal'] + '\n' + library['address'],
                                            'defaultAction': {
                                                'type': 'uri',
                                                'label': '詳細を見る',
                                                'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                                            },
                                            'actions': [{
                                                'type': 'uri',
                                                'label': '詳細を見る',
                                                'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                                            }]
                                        })
                                        break
                            
                            reply_column.append({
                                'title': '検索した書籍',
                                'text': 'ISBN '+ message_text,
                                'defaultAction': {
                                    'type': 'uri',
                                    'label': '詳細を見る',
                                    'uri': 'https://calil.jp/book/' + message_text
                                },
                                'actions': [{
                                    'type': 'uri',
                                    'label': '詳細を見る',
                                    'uri': 'https://calil.jp/book/' + message_text
                                }]
                            })
                            
                            if reply_text == '':
                                message_body = [{
                                    'type': 'text',
                                    'text': 'お気に入り図書館に蔵書は無さそうです。'
                                }]
                            else:
                                message_body = [{
                                    'type': 'text',
                                    'text': 'お気に入り図書館の蔵書の有無と貸出状況をお調べしました。'
                                }]
                                message_body.append({
                                    'type': 'template',
                                    'altText': reply_text,
                                    'template': {
                                        'type': 'carousel',
                                        'columns': reply_column
                                    }
                                })
                elif message_text == '編集する':
                    response = table.get_item(Key = {'userId': event_data['source']['userId']})
                    favorites = response["Item"]['favorites']
                    
                    if len(favorites) == 0:
                        message_body = [{
                            'type': 'text',
                            'text': 'お気に入り図書館はありません。'
                        }]
                    else:
                        reply_text = ''
                        reply_column = []
                        reply_item = [{
                            'type': 'action',
                            'action': {
                                'type': 'message',
                                'label': 'やめる',
                                'text': 'やめる'
                            }
                        },
                        {
                            'type': 'action',
                            'action': {
                                'type': 'message',
                                'label': '全削除',
                                'text': '全削除'
                            }
                        }]
                        
                        for i, library in enumerate(favorites):
                            logger.info(library)
                            reply_text += str(i+1) + '. ' + library['short'] + '\n'
                            reply_column.append({
                                'title': str(i+1) + '. ' + library['short'],
                                'text': library['formal'] + '\n' + library['address'],
                                'defaultAction': {
                                    'type': 'uri',
                                    'label': '詳細を見る',
                                    'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                                },
                                'actions': [{
                                    'type': 'uri',
                                    'label': '詳細を見る',
                                    'uri': 'https://calil.jp/library/' + library['libid'] + '/' + urllib.parse.quote(library['formal'])
                                }]
                            })
                            reply_item.append({
                                'type': 'action',
                                'action': {
                                    'type': 'postback',
                                    'label': str(i+1),
                                    'data': 'action=remove&number=' + str(i+1),
                                    'displayText': str(i+1)
                                }
                            })
                        
                        message_body = [{
                            'type': 'text',
                            'text': 'お気に入り図書館を編集します。\n削除したい図書館の番号を教えて下さい。'
                        }]
                        message_body.append({
                            'type': 'template',
                            'altText': reply_text,
                            'template': {
                                'type': 'carousel',
                                'columns': reply_column
                            },
                            'quickReply': {
                                'items': reply_item
                            }
                        })
                elif message_text == '全削除':
                    remove_all_favorites(event_data)
                    
                    message_body = [{
                        'type': 'text',
                        'text': 'お気に入り図書館を削除しました。'
                    }]
            else:
                continue
        elif event_data['type'] == 'postback':
            postback_data = event_data['postback']['data']
            data_list = postback_data.split('&')
            action = data_list[0].split('=')[1]
            number = data_list[1].split('=')[1]
            
            response = table.get_item(Key = {'userId': event_data['source']['userId']})
            libraries = response["Item"]['libraries']
            favorites = response["Item"]['favorites']
            
            if action == 'add':
                if len(libraries) != 0 and int(number) <= len(libraries):
                    if len(favorites) < max_library:
                        reply_text = ''
                        for i, library in enumerate(favorites):
                            logger.info(library)
                            if library['libid'] == libraries[int(number)-1]['libid']:
                                reply_text = library['short']
                                break
                        
                        if reply_text == '':
                            favorites.append(libraries[int(number)-1])
                            
                            table.update_item(
                                Key = {'userId': event_data['source']['userId']},
                                UpdateExpression = "set libraries=:l, favorites=:f",
                                ExpressionAttributeValues = {
                                    ':l': [],
                                    ':f': favorites
                                },
                                ReturnValues="UPDATED_NEW"
                            )
                        
                            reply_text = libraries[int(number)-1]['short']
                            message_body = [{
                                'type': 'text',
                                'text': number + '. ' + reply_text + '\nをお気に入りに登録しました。'
                            }]
                        else:
                            remove_all_libraries(event_data)
                            
                            message_body = [{
                                'type': 'text',
                                'text': number + '. ' + reply_text + '\nは登録済みです。'
                            }]
                    else:
                        remove_all_libraries(event_data)
                            
                        message_body = [{
                            'type': 'text',
                            'text': 'お気に入り図書館がいっぱいのため、登録できません。\nお気に入り図書館を編集しますか？',
                            'quickReply': {
                                'items': [{
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': 'やめる',
                                        'text': 'やめる'
                                    }
                                },
                                {
                                    'type': 'action',
                                    'action': {
                                        'type': 'message',
                                        'label': '編集する',
                                        'text': '編集する'
                                    }
                                }]
                            }
                        }]
            elif action == 'remove':
                if len(favorites) != 0 and int(number) <= len(favorites):
                    reply_text = favorites[int(number)-1]['short']
                    
                    favorites.pop(int(number)-1)
                    
                    table.update_item(
                        Key = {'userId': event_data['source']['userId']},
                        UpdateExpression = "set favorites=:f",
                        ExpressionAttributeValues = {
                            ':f': favorites
                        },
                        ReturnValues="UPDATED_NEW"
                    )
                    
                    message_body = [{
                        'type': 'text',
                        'text': number + '. ' + reply_text + '\nをお気に入りから削除しました。'
                    }]
            else:
                continue
        else:
            continue
        
        url = 'https://api.line.me/v2/bot/message/reply'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + channel_access_token,
        }
        body = {
            'replyToken': event_data['replyToken'],
            'messages': message_body
        }
        logger.info(message_body)
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST', headers=headers)
        with urllib.request.urlopen(req) as res:
            res_body = res.read().decode('utf-8')
            if res_body != '{}':
                logger.info(res_body)
    
    return {'statusCode': 200, 'body': '{}'}


def remove_all_libraries(event_data):
    table.update_item(
        Key = {'userId': event_data['source']['userId']},
        UpdateExpression = "set libraries=:l",
        ExpressionAttributeValues = {
            ':l': []
        },
        ReturnValues="UPDATED_NEW"
    )


def remove_all_favorites(event_data):
    table.update_item(
        Key = {'userId': event_data['source']['userId']},
        UpdateExpression = "set favorites=:f",
        ExpressionAttributeValues = {
            ':f': []
        },
        ReturnValues="UPDATED_NEW"
    )
