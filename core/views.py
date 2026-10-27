from rest_framework import status
from rest_framework.response import Response


class RepresentationMixin:
    """Write bodies may use a narrower serializer; responses always use the read one.

    Set ``representation_serializer_class`` on the viewset. Writes still go through
    ``perform_create`` / ``perform_update`` so a viewset can hook its own side effects.
    """

    representation_serializer_class = None

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            self.representation_serializer_class(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(self.representation_serializer_class(serializer.instance).data)
