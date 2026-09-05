from io import BytesIO
from uuid import uuid4

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import System, User, UserNote


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def validate_avatar(upload):
    if not upload:
        return upload
    if not isinstance(upload, UploadedFile):
        return upload
    if upload.size > MAX_AVATAR_BYTES:
        raise forms.ValidationError("Image must be 2 MB or smaller.")
    if getattr(upload, "content_type", "") not in ALLOWED_IMAGE_TYPES:
        raise forms.ValidationError("Use a JPEG, PNG, or WebP image.")
    try:
        image = Image.open(upload)
        image.verify()
        upload.seek(0)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError):
        raise forms.ValidationError("The uploaded file is not a valid image.")
    return upload


def normalized_avatar(upload):
    image = Image.open(upload)
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="WEBP", quality=85, method=6)
    return ContentFile(output.getvalue(), name=f"{uuid4().hex}.webp")


class AvatarSaveMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_avatar_name = self.instance.avatar.name if self.instance.pk and self.instance.avatar else ""

    def clean_avatar(self):
        upload = validate_avatar(self.cleaned_data.get("avatar"))
        self._normalized_avatar = None
        if isinstance(upload, UploadedFile):
            try:
                self._normalized_avatar = normalized_avatar(upload)
            except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError):
                raise forms.ValidationError("The image could not be processed. Try another JPEG, PNG, or WebP file.")
        return upload

    def save(self, commit=True):
        instance = super().save(commit=False)
        replacement = getattr(self, "_normalized_avatar", None)
        if replacement:
            instance.avatar = replacement
        if commit:
            instance.save()
            self.save_m2m()
            if replacement and self._original_avatar_name and self._original_avatar_name != instance.avatar.name:
                instance.avatar.storage.delete(self._original_avatar_name)
        return instance


class AdminUserCreateForm(AvatarSaveMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "display_name", "avatar", "is_active", "is_staff")


class AdminUserUpdateForm(AvatarSaveMixin, forms.ModelForm):
    remove_avatar = forms.BooleanField(required=False)

    class Meta:
        model = User
        fields = ("username", "display_name", "avatar", "is_active", "is_staff")

    def save(self, commit=True):
        if self.cleaned_data.get("remove_avatar") and self.instance.avatar:
            self.instance.avatar.delete(save=False)
            self.instance.avatar = ""
        return super().save(commit=commit)


class ProfileForm(AvatarSaveMixin, forms.ModelForm):
    remove_avatar = forms.BooleanField(required=False)

    class Meta:
        model = User
        fields = ("display_name", "avatar")

    def save(self, commit=True):
        if self.cleaned_data.get("remove_avatar") and self.instance.avatar:
            self.instance.avatar.delete(save=False)
            self.instance.avatar = ""
        return super().save(commit=commit)


class SystemForm(forms.ModelForm):
    class Meta:
        model = System
        fields = ("name", "description", "color", "is_active")
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class UserNoteForm(forms.ModelForm):
    class Meta:
        model = UserNote
        fields = ("content",)
        widgets = {"content": forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "maxlength": 1000,
            "placeholder": "Share a note with everyone in the lab…",
        })}
