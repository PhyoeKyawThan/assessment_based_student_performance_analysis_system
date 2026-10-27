from django.db.transaction import atomic
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.filters import uuid_param
from core.views import RepresentationMixin
from marking.models import AssessmentResult
from marking.permissions import IsResultWriter
from marking.serializers import (
    AssessmentResultBulkSerializer,
    AssessmentResultSerializer,
)
from teaching.permissions import is_admin
from users.models import User


class AssessmentResultViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = AssessmentResult.objects.select_related(
        'assessment__distribution__lecture_plan',
        'enrollment__student',
        'enrollment__module',
        'graded_by',
    )
    serializer_class = AssessmentResultSerializer
    representation_serializer_class = AssessmentResultSerializer
    permission_classes = (IsResultWriter,)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if is_admin(user):
            pass
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(assessment__distribution__lecture_plan__lecturer=user)
        else:
            queryset = queryset.filter(enrollment__student=user)

        params = self.request.query_params
        for param, lookup in (
            ('assessment_id', 'assessment'),
            ('enrollment_id', 'enrollment'),
            ('module_id', 'enrollment__module'),
            ('student_id', 'enrollment__student'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})
        return queryset

    @action(detail=False, methods=['post'])
    def bulk(self, request):
        serializer = AssessmentResultBulkSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        with atomic():
            results = serializer.save()
        return Response([
            self.representation_serializer_class(result).data for result in results
        ])
