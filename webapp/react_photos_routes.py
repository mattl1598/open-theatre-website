import io
import json
import time
import urllib
import math
from time import sleep
from datetime import datetime, timedelta

import requests
from corha import corha
from flask_login import current_user, login_required
from PIL import Image
from pprint import pprint

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import aggregate_order_by

from webapp.models import KeyValue, Show, ShowPhotos, StaticMedia, db
from flask import current_app as app
from flask import abort, Blueprint, redirect, render_template, url_for, request, session, jsonify, make_response, copy_current_request_context

from webapp.photos_routes import get_albums, update_access_token
from webapp.react_permissions import check_page_permission

bp = Blueprint("react_photos_routes", __name__)


@bp.get("/members/admin/manage_media")
def manage_media():
	check_page_permission("admin")

	media_db = db.session.query(StaticMedia).order_by(StaticMedia.date.desc()).all()

	if len(media_db):
		media_ids = {
			f"{x.item_id}": f"{x.id}"
			for x in media_db
		}

		update_access_token()

		url = f"https://photoslibrary.googleapis.com/v1/mediaItems:batchGet?mediaItemIds="
		url += f"{'&mediaItemIds='.join(media_ids.keys())}"
		url += f"&access_token={session.get('access_token')}"

		x = requests.get(url)

		thumbs = [
			(
				media_ids[i["mediaItem"]["id"]],
				f'{i["mediaItem"]["baseUrl"]}=d',
				i["mediaItem"]["filename"],
			)
			for i in x.json()["mediaItemResults"]
		]

	else:
		thumbs = []

	data = {
		"type": "manage_media",
		"title": "Manage Media",
		"media": thumbs
	}

	if "react" in request.args.keys():
		return jsonify(data)
	else:
		data["initialData"] = True
		return render_template(
			"react_template.html",
			data=data
		)


@bp.post("/members/api/manage_media/delete")
def delete_static_media():
	check_page_permission("admin")
	media_db = StaticMedia.query.filter_by(id=request.form.get("id")).first()
	StaticMedia.query.filter_by(id=request.form.get("id")).delete()
	update_access_token()
	albums = {b: a for (a, b) in get_albums()}

	if "OADP_DELETE_ME" in albums.keys():
		album_id = albums.get("OADP_DELETE_ME")
	else:
		x = requests.post(
			url=f"https://photoslibrary.googleapis.com/v1/albums?access_token={session.get('access_token')}",
			json={
				"album": {
					"title": "OADP_DELETE_ME"
				}
			}
		)

		album_id = x.json().get("id")
	requests.post(
		url=f"https://photoslibrary.googleapis.com/v1/albums/{album_id}:batchAddMediaItems"
			f"?access_token={session.get('access_token')}",
		json={
			"mediaItemIds": [
				media_db.item_id
			]
		}
	)

	db.session.commit()

	return {
		"code": 200,
		"msg": "Item deleted successfully."
	}


@bp.post("/members/api/manage_media/upload")
def upload_static_media(**kwargs):
	check_page_permission("admin")
	b_in = io.BytesIO()
	if kwargs.get("mammoth") == "true":
		# print("mammoth")
		filename = kwargs.get("image_name").replace(' ', '_')
		b_in = kwargs.get("image")
	else:
		file = request.files.get('fileElem')
		# print(file.content_type)
		filename = file.filename.replace(' ', '_')
		file.save(b_in)
	if filename.rsplit('.', 1)[1] != "webp":
		b_out = io.BytesIO()
		with Image.open(b_in) as im:
			im.save(b_out, format="webp")
	else:
		b_out = b_in

	update_access_token()
	url = "https://photoslibrary.googleapis.com/v1/uploads"
	headers = {
		"Content-type": "application/octet-stream",
		"X-Goog-Upload-Content-Type": "image/webp",
		"X-Goog-Upload-Protocol": "raw"
	}

	x = requests.post(
		url + f"?access_token={session.get('access_token')}",
		headers=headers,
		data=b_out.getvalue()
	)

	albums = {b: a for (a, b) in get_albums()}

	if "OADP_Website_Media" in albums.keys():
		album_id = albums.get("OADP_Website_Media")
	else:
		x = requests.post(
			url=f"https://photoslibrary.googleapis.com/v1/albums?access_token={session.get('access_token')}",
			json={
				"album": {
					"title": "OADP_Website_Media"
				}
			}
		)
		album_id = x.json().get("id")

	url = "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate"
	headers = {
		"Content-type": "application/json"
	}
	data = {
		"albumId": album_id,
		"newMediaItems": [
			{
				"description": "",
				"simpleMediaItem": {
					"fileName": f"{filename.rsplit('.', 1)[0]}.webp",
					"uploadToken": f"{x.content.decode('utf-8')}"
				}
			}
		]
	}

	x = requests.post(
		url + f"?access_token={session.get('access_token')}",
		headers=headers,
		data=json.dumps(data)
	)
	# pprint(x.json())

	new_item = StaticMedia(
		id=StaticMedia.get_new_id(),
		item_id=x.json()["newMediaItemResults"][0]["mediaItem"]["id"],
		filename=x.json()["newMediaItemResults"][0]["mediaItem"]["filename"],
		item_type=x.json()["newMediaItemResults"][0]["mediaItem"]["mimeType"],
		item_dim=",".join(
			[
				x.json()["newMediaItemResults"][0]["mediaItem"]["mediaMetadata"][i]
				for i in ["width", "height"]
			]
		)
	)

	db.session.add(new_item)
	db.session.commit()

	if kwargs.get("mammoth") == "true":
		# print("mammoth")
		return {
			"path": f"/media/{new_item.id}/{new_item.filename}",
			"filename": f"{new_item.filename}"
		}
	else:
		# return jsonify({
		# 	"success": True,
		# 	"url": f"/media/{new_item.id}/{new_item.filename}",
		# 	"filename": f"{new_item.filename}"
		# })
		return {
			"code": 200,
			"msg": "Image Uploaded Successfully"
		}


@bp.get("/members/admin/set_show_photos")
def set_show_photos():
	check_page_permission("admin")

	update_access_token()
	access_token = session.get('access_token')

	url = "https://photoslibrary.googleapis.com/v1/albums"
	y = requests.get(url + f"?access_token={access_token}&pageSize=50").json()
	# pprint(y)

	albums = []
	next_token = "not none"
	while next_token is not None:
		next_token = y.get('nextPageToken')
		for album in y.get("albums"):
			albums.append((album.get("id"), album.get("title"),))

		y = requests.get(url + f"?access_token={access_token}&pageSize=50&pageToken={next_token}").json()

	shows = db.session.query(
		func.json_agg(aggregate_order_by(func.json_build_object(
			"id", Show.id,
			"title", Show.title,
			"date", Show.date
		), Show.date.desc()))
	).scalar()

	data = {
		"type": "show_photos_form",
		"title": "Update Show Photos",
		"shows": shows,
		"albums": albums,
	}

	if "react" in request.args.keys():
		return jsonify(data)
	else:
		data["initialData"] = True
		return render_template(
			"react_template.html",
			data=data
		)


@bp.post("/members/api/admin/set_show_photos")
def set_show_photos_api():
	update_access_token()
	access_token = session.get('access_token')

	data = {
		"albumId": request.form.get("album"),
		"pageSize": 100,
		"access_token": access_token
	}

	url = f"https://photoslibrary.googleapis.com/v1/mediaItems:search?access_token={access_token}"
	z = requests.post(url, data).json()

	next_token = "not none"
	photos = []
	while next_token is not None:
		for photo in z.get("mediaItems"):
			# print(photo.get("id"))
			photo_tuple = (
				list({"photo", "video"}.intersection(set(photo.get("mediaMetadata").keys())))[0],
				photo.get("id"),
				f"{photo.get('mediaMetadata').get('width')},{photo.get('mediaMetadata').get('height')}",
			)
			# print(photo_tuple)
			photos.append(
				photo_tuple
			)
		# print(len(photos))
		data["pageToken"] = (next_token := z.get("nextPageToken"))
		z = requests.post(url, data).json()

	existing_sql = ShowPhotos.query \
		.filter_by(show_id=request.form.get("show")) \
		.all()

	used_ids = [value[0] for value in ShowPhotos.query.with_entities(ShowPhotos.id).all()]
	existing = set([(i.photo_type, i.photo_url, i.photo_desc,) for i in existing_sql])

	to_remove = existing.difference(set(photos))
	to_add = set(photos).difference(existing)

	for photo in to_add:
		new_photo = ShowPhotos(
			id=(new_ID := corha.rand_string(photo[1], 16, used_ids)),
			show_id=request.form.get("show"),
			photo_url=photo[1],
			photo_type=photo[0],
			photo_desc=photo[2]
		)

		used_ids.append(new_ID)
		db.session.add(new_photo)

	for photo in to_remove:
		ShowPhotos.query \
			.filter_by(
				show_id=request.form.get("show"),
				photo_url=photo[1],
				photo_type=photo[0]
			) \
			.delete()

	db.session.commit()

	return {
		"code": 200,
		"msg": "Album Updated Successfully"
	}