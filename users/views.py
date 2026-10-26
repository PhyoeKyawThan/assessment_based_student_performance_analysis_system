from rest_framework_simplejwt.views import TokenObtainPairView
from django.conf import settings

class CustomTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        client_type = request.headers.get('Client-Type', 'mobile').lower()
        
        if response.status_code == 200:
            data = response.data
            access_token = data.get('access')
            refresh_token = data.get('refresh')
            
            if client_type == 'web':
                # Web Client: Secure tokens inside HttpOnly cookies
                response.set_cookie(
                    key='access_token',
                    value=access_token,
                    httponly=True,
                    secure=not settings.DEBUG, 
                    samesite='Lax',
                    max_age=60 * 60 
                )
                response.set_cookie(
                    key='refresh_token',
                    value=refresh_token,
                    httponly=True,
                    secure=not settings.DEBUG,
                    samesite='Lax',
                    max_age=60 * 60 * 24 * 7  
                )
                # Remove raw tokens from the JSON body for web security
                response.data = {"message": "Login successful (HttpOnly cookies set)"}
        return response


# class User