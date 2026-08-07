from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def ordinal(value):

    value = int(value)

    if value == 1:
        return "1st"
    elif value == 2:
        return "2nd"
    elif value == 3:
        return "3rd"
    else:
        return f"{value}th"
    
@register.filter
def short_subject(value):

    if not value:
        return ""

    value = str(value)

    shorts = {
        "Mathematics": "Math",
        "English Language": "Eng",
        "Integrated Science": "Science",
        "Social Studies": "Social",
        "Information & Communication Technology": "ICT",
        "Information  Technology": "I.T",
        "French": "French",
        "History": "Hist.",
        "Religious and Moral Education": "RME",
        "Creative Arts & Design": "C. Arts",
        "Our World Our People": "OWOP",
        "Career Techology": "C. Tech",
        "Ghanaian Language": "Gh. Lang",
        "Geography": "Geo",
        "Biology": "Bio",
        "Chemistry": "Chem",
        "Physics": "Phys",
        "Economics": "Econs",
        "Literature": "Lit",
    }

    return shorts.get(value, value)



@register.filter
def display_stage(value):
    if not value:
        return ""

    return value.replace("_", " ").title()

@register.filter
def short_time(value):

    return value.replace(":00", "").replace(" - ", "-")

