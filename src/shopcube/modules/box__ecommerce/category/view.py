import json
import os

from flask import Blueprint
from flask import current_app
from flask import flash
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import send_from_directory
from flask import url_for

import flask_uploads
import pandas as pd
from flask_login import login_required
from flask_sqlalchemy import sqlalchemy
from shopyo.api.file import delete_file
from shopyo.api.html import notify_success
from shopyo.api.html import notify_warning
from shopyo.api.templates import yo_render
from shopyo.api.validators import is_empty_str
from sqlalchemy import and_

from init import categoryphotos
from init import db
from init import productexcel
from init import subcategoryphotos
from utils.file import unique_sec_filename

from modules.box__default.settings.helpers import get_setting
from modules.box__ecommerce.category.forms import UploadProductForm
from modules.box__ecommerce.category.models import Category
from modules.box__ecommerce.category.models import SubCategory
from modules.box__ecommerce.product.models import Color
from modules.box__ecommerce.product.models import Product
from modules.box__ecommerce.product.models import Size
from modules.resource.models import Resource

dirpath = os.path.dirname(os.path.abspath(__file__))
module_info = {}

with open(dirpath + "/info.json") as f:
    module_info = json.load(f)

globals()["{}_blueprint".format(module_info["module_name"])] = Blueprint(
    "{}".format(module_info["module_name"]),
    __name__,
    template_folder="templates",
    url_prefix=module_info["url_prefix"],
)

module_blueprint = globals()["{}_blueprint".format(module_info["module_name"])]

module_name = module_info["module_name"]


@module_blueprint.route(module_info["dashboard"])
@login_required
def dashboard():
    context = {}
    context["categorys"] = Category.query.all()
    context["active_page"] = get_setting("SECTION_NAME")
    return render_template("category/dashboard.html", **context)


@module_blueprint.route("/add", methods=["GET", "POST"])
@login_required
def add():

    context = {}
    has_category = False

    if request.method == "POST":
        # convert name to lower case and remove leading
        # and trailing spaces
        name = request.form["name"].lower().strip()

        # case 1: do not allow adding empty category name
        if is_empty_str(name):
            flash(notify_warning("Category name cannot be empty"))
            return redirect(url_for("category.add"))

        # case 2: do not allow category name uncategorised
        # not sure if this is needed since if we add this
        # during initialization then this check will be covered
        # by case 3
        if name == "uncategorised" or name == "uncategorized":
            flash(notify_warning("Category cannot be named as uncategorised"))
            return redirect(url_for("category.add"))

        has_category = Category.category_exists(name)

        # case 3: do not allow adding existing category name
        if has_category:
            flash(notify_warning(f'Category "{name}" already exists'))
            return render_template("category/add.html", **context)

        # case 4: sucessfully add the category
        category = Category(name=name)
        try:
            if "photo" in request.files:
                file = request.files["photo"]

                filename = unique_sec_filename(file.filename)
                file.filename = filename
                categoryphotos.save(file)
                category.resources.append(
                    Resource(
                        type="image",
                        filename=filename,
                        category="category_image",
                    )
                )
        except flask_uploads.UploadNotAllowed as e:
            pass

        category.save()
        flash(notify_success(f'Category "{name}" added successfully'))
        return render_template("category/add.html", **context)

    context["has_category"] = str(has_category)
    return render_template("category/add.html", **context)


@module_blueprint.route("<name>/delete", methods=["GET"])
@login_required
def delete(name):

    if is_empty_str(name):
        flash(notify_warning("Cannot delete a category with no name"))
        return redirect(url_for("category.dashboard"))

    if name != "uncategorised":

        category = Category.query.filter(Category.name == name).first()

        if not category:
            flash(notify_warning(f'Category "{name}" does not exist.'))
            return redirect(url_for("category.dashboard"))

        if category.subcategories:
            flash(
                notify_warning(
                    f'Please delete all subcategories for category "{name}"'
                )
            )
            return redirect(url_for("category.dashboard"))

        category.delete()
        flash(notify_success(f'Category "{name}" successfully deleted'))
        return redirect(url_for("category.dashboard"))

    flash(notify_warning("Cannot delete category uncategorised"))
    return redirect(url_for("category.dashboard"))


@module_blueprint.route(
    "/<category_name>/img/<filename>/delete", methods=["GET"]
)
@login_required
def category_image_delete(category_name, filename):
    resource = Resource.query.filter(Resource.filename == filename).first()
    category = Category.query.filter(Category.name == category_name).first()
    category.resources.remove(resource)
    category.update()
    delete_file(
        os.path.join(
            current_app.config["UPLOADED_CATEGORYPHOTOS_DEST"], filename
        )
    )

    return redirect(url_for("category.dashboard"))


@module_blueprint.route("/update", methods=["GET", "POST"])
@login_required
def update():
    context = {}

    if request.method == "POST":
        name = request.form["category_name"]
        old_name = request.form["old_category_name"]
        try:
            category = Category.query.filter_by(name=old_name).first()

            try:
                if "photo" in request.files:
                    file = request.files["photo"]

                    filename = unique_sec_filename(file.filename)
                    file.filename = filename
                    categoryphotos.save(file)
                    category.resources.append(
                        Resource(
                            type="image",
                            filename=filename,
                            category="category_image",
                        )
                    )
            except flask_uploads.UploadNotAllowed as e:
                pass

            category.name = name
            category.update()
        except sqlalchemy.exc.IntegrityError:
            context[
                "message"
            ] = "you cannot modify to an already existing category"
            context["redirect_url"] = "/category/"
            render_template("category/message.html", **context)
        return redirect(url_for("category.dashboard"))


@module_blueprint.route(
    "{}/edit/<category_name>".format(module_info["dashboard"]),
    methods=["GET"],
)
@login_required
def edit_dashboard(category_name):
    context = {}
    category = Category.query.filter(Category.name == category_name).first()

    context.update({"len": len, "category": category})
    return render_template("category/edit.html", **context)


# api
@module_blueprint.route("/check/<category_name>", methods=["GET"])
@login_required
def check(category_name):
    has_category = Category.category_exists(category_name)
    return jsonify({"exists": has_category})


#
# subcategory
#


@module_blueprint.route(
    "{}/<category_name>/sub/".format(module_info["dashboard"]),
    methods=["GET"],
)
@login_required
def manage_sub(category_name):
    """

    Edit subcategory
    """
    context = {}
    category = Category.query.filter(Category.name == category_name).first()

    if category is None:
        flash(notify_warning("category name does not exist"))

    context.update({"category": category})
    return render_template("category/manage_sub.html", **context)


@module_blueprint.route(
    "{}/<category_name>/sub/add".format(module_info["dashboard"]),
    methods=["GET", "POST"],
)
@login_required
def add_sub(category_name):
    if request.method == "POST":

        category = Category.query.filter(
            Category.name == category_name
        ).scalar()

        # case 1: do not allow adding subcategory to nonexisting
        # category
        if category is None:
            return "category does not exist", 400

        # convert name to lower case and remove leading
        # and trailing spaces
        name = request.form["name"].lower().strip()

        # case 2: do not allow adding subcategory with
        # empty name
        if is_empty_str(name):
            flash(notify_warning("Name cannot be empty"))
            return redirect(
                url_for(
                    "category.manage_sub",
                    category_name=category_name,
                )
            )

        existing = (
            SubCategory.query.join(Category)
            .filter(
                and_(SubCategory.name == name, Category.name == category_name)
            )
            .first()
        )

        # case 3: do not allow adding existing subcategory
        # inside a given category
        if existing:
            flash(notify_warning("Name already exists for category"))
            return redirect(
                url_for(
                    "category.manage_sub",
                    category_name=category_name,
                )
            )

        # case 4: successfully add subcategory to desired category
        category = Category.query.filter(
            Category.name == category_name
        ).first()
        subcategory = SubCategory(name=name)

        try:
            if "photo" in request.files:
                file = request.files["photo"]

                filename = unique_sec_filename(file.filename)
                file.filename = filename
                subcategoryphotos.save(file)
                subcategory.resources.append(
                    Resource(
                        type="image",
                        filename=filename,
                        category="subcategory_image",
                    )
                )
        except flask_uploads.UploadNotAllowed as e:
            pass

        category.subcategories.append(subcategory)
        category.update()
    return redirect(
        url_for("category.manage_sub", category_name=category_name)
    )


@module_blueprint.route(
    "{}/sub/<subcategory_id>/img/edit".format(module_info["dashboard"]),
    methods=["GET"],
)
@login_required
def edit_sub_img_dashboard(subcategory_id):
    context = {}
    subcategory = SubCategory.query.get(subcategory_id)
    context.update({"len": len, "subcategory": subcategory})
    return render_template("category/edit_img_sub.html", **context)


@module_blueprint.route(
    "/sub/<subcategory_id>/name/edit", methods=["GET", "POST"]
)
@login_required
def edit_sub_name(subcategory_id):
    if request.method == "POST":
        subcategory = SubCategory.query.get(subcategory_id)
        name = request.form["name"]
        if is_empty_str(name):
            flash(notify_warning("Name cannot be empty"))
            return redirect(
                url_for(
                    "category.manage_sub",
                    category_name=subcategory.category.name,
                )
            )
        category_name = subcategory.category.name
        existing = SubCategory.query.filter(
            (SubCategory.name == name) & (Category.name == category_name)
        ).first()
        if existing:
            flash(notify_warning("Name already exists for category"))
            return redirect(
                url_for(
                    "category.manage_sub",
                    category_name=subcategory.category.name,
                )
            )
        subcategory.name = name
        subcategory.update()
        flash(notify_success("Subcategory name updated successfully!"))
        return redirect(
            url_for(
                "category.manage_sub", category_name=subcategory.category.name
            )
        )


@module_blueprint.route(
    "/sub/<subcategory_id>/img/edit", methods=["GET", "POST"]
)
@login_required
def edit_sub_img(subcategory_id):
    if request.method == "POST":
        subcategory = SubCategory.query.get(subcategory_id)
        try:
            if "photo" in request.files:
                file = request.files["photo"]

                filename = unique_sec_filename(file.filename)
                file.filename = filename
                subcategoryphotos.save(file)
                subcategory.resources.append(
                    Resource(
                        type="image",
                        filename=filename,
                        category="subcategory_image",
                    )
                )
        except flask_uploads.UploadNotAllowed as e:
            pass
        subcategory.update()
        return redirect(
            url_for(
                "category.edit_sub_img_dashboard",
                subcategory_id=subcategory.id,
            )
        )


@module_blueprint.route(
    "/sub/<subcategory_id>/img/<filename>/delete", methods=["GET"]
)
@login_required
def subcategory_image_delete(subcategory_id, filename):
    resource = Resource.query.filter(Resource.filename == filename).first()
    subcategory = SubCategory.query.get(subcategory_id)
    subcategory.resources.remove(resource)
    subcategory.update()
    delete_file(
        os.path.join(
            current_app.config["UPLOADED_SUBCATEGORYPHOTOS_DEST"], filename
        )
    )

    return redirect(
        url_for(
            "category.edit_sub_img_dashboard", subcategory_id=subcategory_id
        )
    )


@module_blueprint.route("/sub/<subcategory_id>/delete", methods=["GET"])
@login_required
def sub_delete(subcategory_id):
    subcategory = SubCategory.query.get(subcategory_id)
    category_name = subcategory.category.name
    if (
        subcategory.name == "uncategorised"
        and subcategory.category.name == "uncategorised"
    ):
        flash(
            notify_warning(
                "Cannot delete subcategory uncategorised "
                + "of category uncategorised"
            )
        )
        return redirect(
            url_for("category.manage_sub", category_name=category_name)
        )

    uncategorised_sub = (
        SubCategory.query.join(Category)
        .filter(
            and_(
                SubCategory.name == "uncategorised",
                Category.name == "uncategorised",
            )
        )
        .first()
    )

    # before removing the subcategory, move the products
    # in this subcategory to uncategorised subcategory
    for product in subcategory.products:
        uncategorised_sub.products.append(product)

    subcategory.products = []
    db.session.delete(subcategory)
    db.session.commit()

    # for resource in subcategory.resources:
    #     filename = resource.filename
    #     resource.delete()
    #     delete_file(
    #         os.path.join(
    #             current_app.config["UPLOADED_SUBCATEGORYPHOTOS_DEST"],
    #             filename
    #         )
    #     )
    # subcategory.delete()

    # add for products change
    return redirect(
        url_for("category.manage_sub", category_name=category_name)
    )


@module_blueprint.route(
    "<category_id>/{}/sub".format(module_info["dashboard"]),
    methods=["GET"],
)
@login_required
def choose_sub_dashboard(category_id):
    context = {}
    category = Category.query.get(category_id)

    context.update({"category": category})
    return render_template("category/choose_sub.html", **context)


#
# serve files
#


@module_blueprint.route("/sub/file/<filename>", methods=["GET"])
@login_required
def subcategory_image(filename):
    if filename == "default":
        return send_from_directory(
            os.path.join(current_app.config["BASE_DIR"], "static"),
            "logo.png",
        )
    return send_from_directory(
        current_app.config["UPLOADED_SUBCATEGORYPHOTOS_DEST"], filename
    )


@module_blueprint.route("/file/<filename>", methods=["GET"])
@login_required
def category_image(filename):

    return send_from_directory(
        current_app.config["UPLOADED_CATEGORYPHOTOS_DEST"], filename
    )


#
# Upload
#


@module_blueprint.route("/upload/", methods=["GET", "POST"])
@login_required
def upload():
    product_form = UploadProductForm()
    return yo_render("category/upload.html", locals())


def isdiscontinued(cell_value):
    cell_value = str(cell_value)

    if cell_value == "yes":
        return True
    if cell_value == "1":
        return True
    return False


@module_blueprint.route("/upload/check", methods=["GET", "POST"])
@login_required
def upload_check():
    form = UploadProductForm()
    if request.method == "POST":
        if form.validate_on_submit():
            filename = productexcel.save(
                request.files[form.product_file.data.name]
            )
            file_path = os.path.join(
                current_app.config["UPLOADED_PRODUCTEXCEL_DEST"], filename
            )

            xls = pd.ExcelFile(file_path)

            products = pd.read_excel(xls, xls.sheet_names[0])

            for i, row in products.iterrows():
                barcode = str(row[0]).strip()
                name = str(row[1]).strip()
                description = str(row[2]).strip()
                colors = str(row[3]).strip()
                sizes = str(row[4]).strip()
                price = str(row[5]).strip()
                selling_price = str(row[6]).strip()
                in_stock = str(row[7]).strip()
                discontinued = str(row[8]).strip()
                category_name = str(row[9]).strip()
                subcategory_name = str(row[10]).strip()

                discontinued = isdiscontinued(row[8])

                with db.session.no_autoflush:
                    product = Product.query.filter(
                        Product.barcode == barcode
                    ).first()
                    category = Category.query.filter(
                        Category.name == category_name
                    ).first()
                    subcategory = SubCategory.query.filter(
                        SubCategory.name == subcategory_name
                    ).first()
                    already_existed = False

                    if not subcategory:
                        subcategory = SubCategory(name=subcategory_name)

                    if not category:
                        category = Category(name=category_name)

                    if not product:
                        already_existed = True
                        product = Product()

                    product.barcode = barcode
                    product.name = name
                    product.description = description
                    # product.date = date

                    if not str(price).strip():
                        price = 0
                    product.price = price
                    product.selling_price = selling_price
                    product.in_stock = in_stock
                    product.discontinued = discontinued

                    product.sizes.clear()
                    sizes = sizes.strip().strip("\n")
                    sizes = [
                        s.strip("\r") for s in sizes.split("\n") if s.strip()
                    ]
                    sizes = [
                        Size(name=s, product_id=product.id) for s in sizes
                    ]
                    product.sizes.extend(sizes)

                    product.colors.clear()
                    colors = colors.strip().strip("\n")
                    colors = [
                        c.strip("\r") for c in colors.split("\n") if c.strip()
                    ]
                    colors = [
                        Color(name=c, product_id=product.id) for c in colors
                    ]
                    product.colors.extend(colors)

                category.subcategories.append(subcategory)
                subcategory.products.append(product)
            db.session.add(category)
            db.session.add(subcategory)
            db.session.add(product)

            print(product, subcategory, category)

            db.session.commit()

            flash(
                notify_success(
                    f"Products uploaded: {form.product_file.data.name}"
                )
            )
            os.remove(file_path)
        else:
            flash_errors(form)
    return redirect(url_for("category.upload"))
