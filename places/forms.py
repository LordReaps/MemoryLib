from django import forms

from .models import Place


class PlaceForm(forms.ModelForm):
    class Meta:
        model = Place
        fields = [
            "title",
            "description",
            "latitude",
            "longitude",
            "start_date",
            "end_date",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Название места", "autocomplete": "off"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Описание (необязательно)"}
            ),
            "latitude": forms.HiddenInput(),
            "longitude": forms.HiddenInput(),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }
