from rest_framework import serializers


class ReportQuerySerializer(serializers.Serializer):
    query = serializers.CharField(max_length=4000, trim_whitespace=True)
    session = serializers.CharField(max_length=255, trim_whitespace=True)
    user_id = serializers.CharField(
        max_length=255,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
    )
    include_tool_results = serializers.BooleanField(required=False, default=True)

    def validate_query(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Query is required.")
        return value

    def validate_session(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Session is required.")
        return value
