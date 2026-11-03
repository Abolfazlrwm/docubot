import factory
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document
from apps.users.models import User

PASSWORD = "s3creT!pass!"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    # Store the hash directly so no post-generation save hook is needed.
    password = factory.LazyFunction(lambda: make_password(PASSWORD))


class DocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Document

    owner = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Document {n}")
    file = factory.LazyAttribute(
        lambda o: SimpleUploadedFile(f"{o.title}.txt", b"hello docubot", content_type="text/plain")
    )
    original_filename = factory.LazyAttribute(lambda o: o.file.name)
    content_type = "text/plain"
    size_bytes = factory.LazyAttribute(lambda o: o.file.size)
